"""Small desktop dashboard backed by Golmorad's existing SQLite services."""

from __future__ import annotations

import queue
import re
import json
import math
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from Database.DataSeeder import DataSeeder
from Database.SQLiteUtils import SQLiteUtils
from TradeUtils.FundamentalAnalyzer import FundamentalAnalyzer
from TradeUtils.RiskManager import RiskManager
from TradeUtils.StrategyAnalyzer import StrategyAnalyzer
from TradeUtils.PaperTrading import PaperTrader


class GolmoradDashboard:
    """Read stored market data, run analysis, and refresh sources without freezing the UI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.database = SQLiteUtils()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.root.title("Golmorad — Market Research")
        self.root.geometry("1000x720")
        self.root.minsize(760, 560)

        self.symbol = tk.StringVar(value="BTC-USD")
        self.interval = tk.StringVar(value="1d")
        self.capital = tk.StringVar()
        self.activity = tk.StringVar(value="Ready. Analysis uses data already saved in the local database.")
        self._build()
        self.root.after(150, self._poll_messages)
        self.refresh_status()
        self.analyze()
        self.load_candles()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Golmorad", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Paper mode by default · Bit24 not connected · Live trading disabled", foreground="#596579").pack(anchor="w", pady=(0, 12))

        controls = ttk.LabelFrame(outer, text="Analysis", padding=10)
        controls.pack(fill="x")
        ttk.Label(controls, text="Symbol").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.symbol, width=16).grid(row=0, column=1, padx=(6, 14))
        ttk.Label(controls, text="Candle interval").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.interval, width=8).grid(row=0, column=3, padx=(6, 14))
        ttk.Label(controls, text="Capital (optional)").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.capital, width=14).grid(row=0, column=5, padx=(6, 14))
        self.analyze_button = ttk.Button(controls, text="Analyze saved data", command=self.analyze)
        self.analyze_button.grid(row=0, column=6, padx=4)
        self.refresh_button = ttk.Button(controls, text="Refresh data", command=self.refresh_data)
        self.refresh_button.grid(row=0, column=7, padx=4)
        self.paper_button = ttk.Button(controls, text="Run paper once", command=self.run_paper)
        self.paper_button.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(controls, text="Paper status", command=self.paper_status).grid(row=1, column=2, pady=(8, 0))
        ttk.Button(controls, text="Emergency stop", command=self.emergency_stop).grid(row=1, column=3, pady=(8, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=12)
        dashboard_tab = ttk.Frame(notebook, padding=4)
        candles_tab = ttk.Frame(notebook, padding=8)
        notebook.add(dashboard_tab, text="Dashboard")
        notebook.add(candles_tab, text="Market data")

        panes = ttk.Panedwindow(dashboard_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        analysis_frame = ttk.LabelFrame(panes, text="Analysis report", padding=8)
        status_frame = ttk.LabelFrame(panes, text="Recent source activity", padding=8)
        panes.add(analysis_frame, weight=3)
        panes.add(status_frame, weight=2)

        self.report = tk.Text(analysis_frame, wrap="word", height=20, padx=10, pady=10,
                              font=("Segoe UI", 10), state="disabled")
        report_scroll = ttk.Scrollbar(analysis_frame, orient="vertical", command=self.report.yview)
        self.report.configure(yscrollcommand=report_scroll.set)
        self.report.pack(side="left", fill="both", expand=True)
        report_scroll.pack(side="right", fill="y")

        columns = ("dataset", "status", "details", "finished", "rows")
        self.status_table = ttk.Treeview(status_frame, columns=columns, show="headings", height=16)
        for column, title, width in (("dataset", "Source", 130), ("status", "Result", 75),
                                     ("details", "Error detail", 180),
                                     ("finished", "Finished (UTC)", 140), ("rows", "Rows", 55)):
            self.status_table.heading(column, text=title)
            self.status_table.column(column, width=width, anchor="w")
        status_scroll = ttk.Scrollbar(status_frame, orient="vertical", command=self.status_table.yview)
        self.status_table.configure(yscrollcommand=status_scroll.set)
        self.status_table.pack(side="left", fill="both", expand=True)
        status_scroll.pack(side="right", fill="y")
        ttk.Button(status_frame, text="Reload status", command=self.refresh_status).pack(anchor="e", pady=(8, 0))

        candle_toolbar = ttk.Frame(candles_tab)
        candle_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(candle_toolbar, text="Most recent saved candles for the selected analysis settings.").pack(side="left")
        ttk.Button(candle_toolbar, text="Load candles", command=self.load_candles).pack(side="right")
        candle_columns = ("timestamp", "open", "high", "low", "close", "volume", "source")
        self.candle_table = ttk.Treeview(candles_tab, columns=candle_columns, show="headings", height=18)
        for column, title, width in (("timestamp", "Timestamp (UTC)", 190), ("open", "Open", 105),
                                     ("high", "High", 105), ("low", "Low", 105),
                                     ("close", "Close", 105), ("volume", "Volume", 140),
                                     ("source", "Source", 110)):
            self.candle_table.heading(column, text=title)
            self.candle_table.column(column, width=width, anchor="e" if column in {"open", "high", "low", "close", "volume"} else "w")
        candle_scroll = ttk.Scrollbar(candles_tab, orient="vertical", command=self.candle_table.yview)
        self.candle_table.configure(yscrollcommand=candle_scroll.set)
        self.candle_table.pack(side="left", fill="both", expand=True)
        candle_scroll.pack(side="right", fill="y")

        ttk.Separator(outer).pack(fill="x")
        ttk.Label(outer, textvariable=self.activity, anchor="w", padding=(0, 8)).pack(fill="x")

    def _set_report(self, text: str) -> None:
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def analyze(self) -> None:
        symbol, interval = self.symbol.get().strip(), self.interval.get().strip()
        if not symbol or not interval:
            messagebox.showerror("Invalid analysis settings", "Enter both a symbol and candle interval.", parent=self.root)
            return
        capital_text = self.capital.get().strip()
        try:
            capital = float(capital_text) if capital_text else None
            if capital is not None and capital <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid capital", "Capital must be a number greater than zero.", parent=self.root)
            return

        self.analyze_button.configure(state="disabled")
        self.activity.set("Analyzing saved data…")
        self._run_background("analysis", lambda: self._make_report(symbol, interval, capital))

    def _make_report(self, symbol: str, interval: str, capital: float | None) -> str:
        self.database.initialize()
        result = StrategyAnalyzer().analyze(symbol, interval)
        fundamental = FundamentalAnalyzer(self.database).analyze(news_limit=8, sentiment_limit=30)
        lines = [
            f"{result['symbol']} · {result['interval']}",
            f"Signal: {result['signal'].upper()}",
            f"Reason: {result['reason']}",
            f"Latest candle: {result.get('as_of') or 'No saved candle'}",
            f"Data source: {result.get('source') or 'Unknown'}",
        ]
        technical = result.get("technical", {})
        freshness = result.get("freshness") or technical.get("freshness") or {}
        lines.extend((f"Freshness: {freshness.get('status', 'unknown')}",
                      f"Saved candles: {technical.get('observations', 0)}"))
        quality = result.get("data_quality") or technical.get("data_quality") or {}
        lines.append(f"Data quality: {quality.get('status', 'unknown')}"
                     + (f" · {quality['reason']}" if quality.get("reason") else ""))
        indicators = technical.get("indicators", {})
        if technical.get("close") is not None:
            lines.append(f"Latest close: {technical['close']:,.4f}")
        for name in ("sma_50", "ema_12", "ema_26", "rsi_14", "atr_14"):
            value = indicators.get(name)
            lines.append(f"{name.upper()}: {value:,.4f}" if value is not None else f"{name.upper()}: unavailable")
        plan = result.get("trade_plan")
        if plan:
            lines.extend(("", "Trade plan (research only):",
                          f"Entry: {plan['entry_price']:,.4f}",
                          f"Stop: {plan['stop_price']:,.4f}",
                          f"Target: {plan['target_price']:,.4f}",
                          f"Risk/reward: {plan['risk_reward_ratio']:.1f}:1"))
            if capital is not None:
                size = RiskManager.calculate_position_size(
                    capital, plan["entry_price"], plan["stop_price"])
                lines.extend((f"Position size: {size['quantity']:.8f}",
                              f"Estimated exposure: {size['exposure']:,.2f}",
                              f"Planned account risk: {size['actual_risk_percent']:.2f}%"))
        elif capital is not None:
            lines.append("Position size: not calculated because there is no long setup.")

        sentiment = fundamental.get("sentiment")
        lines.extend(("", f"Saved news items: {fundamental['news_count']}"))
        if sentiment:
            lines.append(f"Latest fear/greed: {sentiment['value']} ({sentiment['classification']}) · {sentiment['observed_at']}")
        else:
            lines.append("Latest fear/greed: unavailable")
        lines.extend(("", "Signals are rule-based research outputs. No live orders are placed."))
        return "\n".join(lines)

    def refresh_status(self) -> None:
        self._run_background("status", self._read_status)

    def _read_status(self) -> list[dict[str, Any]]:
        self.database.initialize()
        return self.database.get_sync_runs(limit=30)

    def refresh_data(self) -> None:
        self.refresh_button.configure(state="disabled")
        self.activity.set("Refreshing configured sources in the background…")
        self._run_background("refresh", lambda: DataSeeder(self.database).run_once())

    def run_paper(self) -> None:
        try:
            capital = float(self.capital.get().strip())
            if not math.isfinite(capital) or capital <= 0:
                raise ValueError
        except (ValueError, TypeError):
            messagebox.showerror("Paper capital required", "Enter a finite positive simulated capital amount first.", parent=self.root)
            return
        self.paper_button.configure(state="disabled")
        self.activity.set("Processing one paper decision in the background; no exchange order is possible.")
        symbol, interval = self.symbol.get().strip(), self.interval.get().strip()
        self._run_background("paper", lambda: PaperTrader(self.database).run_once(
            symbol, interval, capital))

    def paper_status(self) -> None:
        self._run_background("paper_status", lambda: PaperTrader(self.database).status())

    def emergency_stop(self) -> None:
        self._run_background("paper_stop", self._stop_paper)

    def _stop_paper(self) -> dict[str, Any]:
        trader = PaperTrader(self.database)
        trader.stop("operator_stop_gui")
        return trader.status()

    def load_candles(self) -> None:
        symbol, interval = self.symbol.get().strip(), self.interval.get().strip()
        if not symbol or not interval:
            messagebox.showerror("Invalid market data settings", "Enter both a symbol and candle interval.", parent=self.root)
            return
        self.activity.set(f"Loading saved {symbol} {interval} candles…")
        self._run_background("candles", lambda: self.database.get_candles(symbol, interval, limit=200))

    def _run_background(self, action: str, work: Callable[[], Any]) -> None:
        def run() -> None:
            try:
                self.messages.put((action, work()))
            except Exception as exc:
                self.messages.put((f"{action}_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=run, name=f"golmorad-{action}", daemon=True).start()

    def _poll_messages(self) -> None:
        try:
            while True:
                action, value = self.messages.get_nowait()
                if action == "analysis":
                    self._set_report(value)
                    self.analyze_button.configure(state="normal")
                    self.activity.set("Analysis complete.")
                elif action == "status":
                    self._show_status(value)
                elif action in {"paper", "paper_status", "paper_stop"}:
                    if action == "paper":
                        self.paper_button.configure(state="normal")
                    self._set_report(json.dumps(value, ensure_ascii=False, indent=2))
                    self.activity.set({"paper": "Paper decision complete.",
                                       "paper_status": "Paper account and event history loaded.",
                                       "paper_stop": "Emergency stop active; paper orders are blocked."}[action])
                elif action == "candles":
                    for item in self.candle_table.get_children():
                        self.candle_table.delete(item)
                    for candle in reversed(value):
                        prices = (f"{candle[key]:,.4f}" for key in ("open", "high", "low", "close"))
                        self.candle_table.insert("", "end", values=(
                            candle["timestamp"], *prices, f"{candle['volume']:,.0f}",
                            candle.get("source") or "Unknown"))
                    self.activity.set(f"Loaded {len(value)} saved candles." if value else "No candles are stored for these settings.")
                elif action == "refresh":
                    self.refresh_button.configure(state="normal")
                    failed = [name for name, count in value.items() if count == 0]
                    summary = ", ".join(f"{name}: {count}" for name, count in value.items())
                    self.activity.set("Refresh finished. Some sources returned no rows." if failed else "Refresh finished successfully.")
                    if summary:
                        self.activity.set(f"Refresh finished · {summary}")
                    self.refresh_status()
                    self.analyze()
                elif action.endswith("_error"):
                    operation = action.removesuffix("_error")
                    if operation == "analysis":
                        self.analyze_button.configure(state="normal")
                    if operation == "refresh":
                        self.refresh_button.configure(state="normal")
                    if operation == "paper":
                        self.paper_button.configure(state="normal")
                    self.activity.set(f"{operation.title()} failed; see the error dialog.")
                    error_type = str(value).split(":", 1)[0][:60]
                    messagebox.showerror(f"{operation.title()} failed",
                                         f"Golmorad could not complete this operation ({error_type}). "
                                         "Check the application log for details.", parent=self.root)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_messages)

    def _show_status(self, runs: list[dict[str, Any]]) -> None:
        for item in self.status_table.get_children():
            self.status_table.delete(item)
        for run in runs:
            error = run.get("error")
            if error:
                error_type, separator, detail = error.partition(":")
                state = f"failed ({error_type[:24]})"
                safe_detail = re.sub(r"https?://\S+", "<URL>", detail.strip())
                safe_detail = re.sub(r"(?i)(api[_ -]?key|token|secret|password)\s*[:=]\s*\S+",
                                     r"\1=<redacted>", safe_detail)
                safe_detail = safe_detail[:120] if separator else ""
            else:
                state = run["status"]
                safe_detail = ""
            self.status_table.insert("", "end", values=(
                run["dataset"], state, safe_detail,
                run.get("finished_at") or run["started_at"], run["rows_inserted"]))
        if not runs:
            self.activity.set("No source runs are recorded yet. Use Refresh data to start a collection cycle.")


def launch() -> None:
    root = tk.Tk()
    GolmoradDashboard(root)
    root.mainloop()
