#!/usr/bin/env python3
"""
core/ib_extended.py — Full IB API surface: PnL streams, contract details,
fundamentals, news, WSH calendar, head timestamps, market rules, what-if margin.

Throttled + cached (models/ib_extended_cache.json). Slow pulls run off-hours;
light pulls (PnL single, contract details) refresh during RTH for held symbols.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig
    from core.connector import IBConnector

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
CACHE_PATH = MODELS_DIR / "ib_extended_cache.json"
_lock = threading.RLock()

_LIGHT_TTL = float(os.getenv("IB_EXTENDED_LIGHT_TTL_SEC", "90"))
_FULL_TTL = float(os.getenv("IB_EXTENDED_FULL_TTL_SEC", "3600"))


def ib_extended_enabled() -> bool:
    return os.getenv("IB_EXTENDED_ENABLED", "true").lower() in ("1", "true", "yes")


def market_rules_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    """reqMarketRule often 322 on paper Gateway — skip unless explicitly enabled."""
    raw = os.getenv("IB_EXTENDED_MARKET_RULES", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if cfg is not None and getattr(cfg, "PAPER_TRADING", False):
        return False
    return True


def _primary_account(ib) -> str:
    try:
        for v in ib.accountValues():
            if str(getattr(v, "tag", "") or "") == "AccountCode":
                return str(getattr(v, "value", "") or "")
    except Exception:
        pass
    try:
        for p in ib.portfolio():
            acct = str(getattr(p, "account", "") or "")
            if acct:
                return acct
    except Exception:
        pass
    return ""


def _load_cache() -> Dict[str, Any]:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(data: Dict[str, Any]) -> None:
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        log.debug(f"ib_extended cache save: {exc}")


def get_extended_cache() -> Dict[str, Any]:
    with _lock:
        return dict(_load_cache())


@dataclass
class IBExtendedBundle:
    account_pnl: Dict[str, Any] = field(default_factory=dict)
    position_pnl: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    contract_details: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fundamentals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    news_bulletins: List[Dict[str, Any]] = field(default_factory=list)
    news_historical: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    wsh_events: List[Dict[str, Any]] = field(default_factory=list)
    head_timestamps: Dict[str, str] = field(default_factory=dict)
    market_rules: Dict[str, List[float]] = field(default_factory=dict)
    quote_snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    account_summary: Dict[str, float] = field(default_factory=dict)
    completed_orders: List[Dict[str, Any]] = field(default_factory=list)
    news_providers: List[Dict[str, str]] = field(default_factory=list)
    refreshed_at: float = 0.0
    refresh_mode: str = ""


def _contract_for_symbol(connector: "IBConnector", symbol: str):
    return connector.get_contract(symbol)


def fetch_account_pnl(ib, account: str) -> Dict[str, Any]:
    """One-shot reqPnL snapshot (cancel immediately)."""
    out: Dict[str, Any] = {}
    if not account:
        return out
    pnl_obj = None
    try:
        pnl_obj = ib.reqPnL(account)
        ib.sleep(0.4)
        out = {
            "daily_pnl": round(float(getattr(pnl_obj, "dailyPnL", 0) or 0), 2),
            "unrealized_pnl": round(float(getattr(pnl_obj, "unrealizedPnL", 0) or 0), 2),
            "realized_pnl": round(float(getattr(pnl_obj, "realizedPnL", 0) or 0), 2),
        }
    except Exception as exc:
        log.debug(f"reqPnL: {exc}")
    finally:
        if pnl_obj is not None:
            try:
                ib.cancelPnL(account)
            except Exception:
                pass
    return out


def fetch_position_pnl_single(
    ib,
    account: str,
    positions: List[Any],
) -> Dict[str, Dict[str, Any]]:
    """reqPnLSingle per open position — IB broker PnL per symbol."""
    out: Dict[str, Dict[str, Any]] = {}
    if not account:
        return out
    for pos in positions:
        sym = (getattr(getattr(pos, "contract", None), "symbol", "") or "").upper()
        con_id = int(getattr(getattr(pos, "contract", None), "conId", 0) or 0)
        if not sym or con_id <= 0:
            continue
        pnl_obj = None
        try:
            pnl_obj = ib.reqPnLSingle(account, "", con_id)
            ib.sleep(0.25)
            out[sym] = {
                "con_id": con_id,
                "daily_pnl": round(float(getattr(pnl_obj, "dailyPnL", 0) or 0), 2),
                "unrealized_pnl": round(float(getattr(pnl_obj, "unrealizedPnL", 0) or 0), 2),
                "realized_pnl": round(float(getattr(pnl_obj, "realizedPnL", 0) or 0), 2),
                "value": round(float(getattr(pnl_obj, "value", 0) or 0), 2),
            }
        except Exception as exc:
            log.debug(f"reqPnLSingle {sym}: {exc}")
        finally:
            if pnl_obj is not None:
                try:
                    ib.cancelPnLSingle(account, "", con_id)
                except Exception:
                    pass
    return out


def fetch_contract_details(ib, connector: "IBConnector", symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols[:12]:
        sym = sym.upper()
        if not sym:
            continue
        try:
            contract = _contract_for_symbol(connector, sym)
            details = ib.reqContractDetails(contract)
            if not details:
                continue
            cd = details[0]
            c = cd.contract
            out[sym] = {
                "con_id": int(getattr(c, "conId", 0) or 0),
                "long_name": str(getattr(cd, "longName", "") or "")[:120],
                "exchange": str(getattr(c, "primaryExchange", "") or getattr(c, "exchange", "")),
                "min_tick": float(getattr(cd, "minTick", 0) or 0),
                "time_zone": str(getattr(cd, "timeZoneId", "") or ""),
                "trading_hours": str(getattr(cd, "tradingHours", "") or "")[:200],
                "liquid_hours": str(getattr(cd, "liquidHours", "") or "")[:200],
                "industry": str(getattr(cd, "industry", "") or ""),
                "category": str(getattr(cd, "category", "") or ""),
            }
        except Exception as exc:
            log.debug(f"reqContractDetails {sym}: {exc}")
    return out


def _parse_fundamental_xml(xml_text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not xml_text:
        return out
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if elem.text and tag in (
                "MarketCap", "PE", "EPS", "SharesOutstanding", "Revenue",
                "QuarterlyRevenueGrowth", "GrossMargin", "NetMargin",
            ):
                try:
                    out[tag] = float(elem.text.replace(",", ""))
                except ValueError:
                    out[tag] = elem.text[:80]
    except Exception:
        for key in ("MarketCap", "PE", "EPS"):
            m = re.search(rf"<{key}>([^<]+)</{key}>", xml_text)
            if m:
                try:
                    out[key] = float(m.group(1).replace(",", ""))
                except ValueError:
                    out[key] = m.group(1)
    return out


def fetch_fundamentals(ib, connector: "IBConnector", symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    reports = [
        r.strip()
        for r in os.getenv("IB_FUNDAMENTAL_REPORTS", "ReportSnapshot,Ratios").split(",")
        if r.strip()
    ]
    for sym in symbols[:6]:
        sym = sym.upper()
        merged: Dict[str, Any] = {}
        for report in reports[:2]:
            try:
                contract = _contract_for_symbol(connector, sym)
                xml_text = ib.reqFundamentalData(contract, report)
                ib.sleep(0.45)
                parsed = _parse_fundamental_xml(xml_text or "")
                if parsed:
                    parsed["report"] = report
                    merged.update(parsed)
                try:
                    ib.cancelFundamentalData(contract)
                except Exception:
                    pass
            except Exception as exc:
                log.debug(f"reqFundamentalData {sym}/{report}: {exc}")
        if merged:
            out[sym] = merged
    return out


def fetch_quote_snapshots(ib, connector: "IBConnector", symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """reqTickers one-shot — bid/ask/last/volume from IB for held symbols."""
    out: Dict[str, Dict[str, Any]] = {}
    contracts = []
    sym_order: List[str] = []
    for sym in symbols[:10]:
        sym = sym.upper()
        try:
            contracts.append(_contract_for_symbol(connector, sym))
            sym_order.append(sym)
        except Exception:
            continue
    if not contracts:
        return out
    try:
        qualified = ib.qualifyContracts(*contracts)
        tickers = ib.reqTickers(*qualified)
        ib.sleep(0.35)
        for t in tickers:
            sym = (getattr(getattr(t, "contract", None), "symbol", "") or "").upper()
            if not sym:
                continue
            out[sym] = {
                "bid": round(float(getattr(t, "bid", 0) or 0), 4),
                "ask": round(float(getattr(t, "ask", 0) or 0), 4),
                "last": round(float(getattr(t, "last", 0) or 0), 4),
                "close": round(float(getattr(t, "close", 0) or 0), 4),
                "volume": float(getattr(t, "volume", 0) or 0),
            }
        # reqTickers is snapshot-only — no streaming reqId; cancelMktData spams IB noise.
    except Exception as exc:
        log.debug(f"reqTickers quotes: {exc}")
    return out


def fetch_account_summary(ib, account: str) -> Dict[str, float]:
    """reqAccountSummary group tags — supplements accountValues."""
    out: Dict[str, float] = {}
    if not account:
        return out
    req_id = 9001
    try:
        ib.reqAccountSummary(req_id, "All", "$LEDGER:ALL")
        ib.sleep(0.35)
        for row in ib.accountSummary():
            if str(getattr(row, "account", "") or "") not in (account, ""):
                continue
            tag = str(getattr(row, "tag", "") or "")
            try:
                out[tag] = round(float(getattr(row, "value", 0) or 0), 4)
            except (TypeError, ValueError):
                pass
        ib.cancelAccountSummary(req_id)
    except Exception as exc:
        log.debug(f"reqAccountSummary: {exc}")
    return out


def fetch_completed_orders(ib, limit: int = 15) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        ib.reqCompletedOrders(True)
        ib.sleep(0.25)
    except Exception as exc:
        log.debug(f"reqCompletedOrders: {exc}")
    try:
        for t in list(ib.trades())[-limit * 2:]:
            status = getattr(getattr(t, "orderStatus", None), "status", "") or ""
            if status not in ("Filled", "Cancelled", "ApiCancelled"):
                continue
            sym = (getattr(getattr(t, "contract", None), "symbol", "") or "").upper()
            order = getattr(t, "order", None)
            out.append({
                "symbol": sym,
                "status": status,
                "action": str(getattr(order, "action", "") or ""),
                "qty": float(getattr(order, "totalQuantity", 0) or 0),
                "avg_fill": float(getattr(getattr(t, "orderStatus", None), "avgFillPrice", 0) or 0),
            })
            if len(out) >= limit:
                break
    except Exception as exc:
        log.debug(f"completed orders parse: {exc}")
    return out


def fetch_news_providers_list(ib) -> List[Dict[str, str]]:
    try:
        providers = ib.reqNewsProviders()
        return [
            {"code": str(getattr(p, "code", "")), "name": str(getattr(p, "name", ""))[:60]}
            for p in (providers or [])
        ]
    except Exception as exc:
        log.debug(f"reqNewsProviders: {exc}")
        return []


def fetch_news_bulletins(ib) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        ib.reqNewsBulletins(True)
        ib.sleep(0.5)
        for nb in ib.newsBulletins():
            out.append({
                "time": str(getattr(nb, "time", "") or ""),
                "code": str(getattr(nb, "msgCode", "") or ""),
                "message": str(getattr(nb, "message", "") or "")[:500],
            })
        ib.cancelNewsBulletins()
    except Exception as exc:
        log.debug(f"reqNewsBulletins: {exc}")
    return out[:30]


def fetch_historical_news(ib, connector: "IBConnector", symbols: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        providers = ib.reqNewsProviders()
        codes = "+".join([str(getattr(p, "code", "")) for p in providers[:4] if getattr(p, "code", "")])
    except Exception:
        codes = ""
    if not codes:
        return out
    for sym in symbols[:5]:
        sym = sym.upper()
        try:
            contract = _contract_for_symbol(connector, sym)
            con_id = int(getattr(contract, "conId", 0) or 0)
            if con_id <= 0:
                ib.qualifyContracts(contract)
                con_id = int(getattr(contract, "conId", 0) or 0)
            if con_id <= 0:
                continue
            articles = ib.reqHistoricalNews(con_id, codes, "", "", int(os.getenv("IB_NEWS_MAX", "8")))
            rows = []
            for a in articles or []:
                rows.append({
                    "time": str(getattr(a, "time", "") or ""),
                    "provider": str(getattr(a, "providerCode", "") or ""),
                    "article_id": str(getattr(a, "articleId", "") or ""),
                    "headline": str(getattr(a, "headline", "") or "")[:300],
                })
            if rows:
                out[sym] = rows
        except Exception as exc:
            log.debug(f"reqHistoricalNews {sym}: {exc}")
    return out


def fetch_wsh_events(ib, connector: "IBConnector", symbols: List[str]) -> List[Dict[str, Any]]:
    """Wall Street Horizon earnings/corporate events."""
    out: List[Dict[str, Any]] = []
    try:
        meta = ib.reqWshMetaData()
        _ = meta  # subscription ack
        ib.sleep(0.3)
    except Exception as exc:
        log.debug(f"reqWshMetaData: {exc}")
        return out
    try:
        from core.ib_client import WshEventData
    except ImportError:
        return out
    for sym in symbols[:8]:
        sym = sym.upper()
        try:
            contract = _contract_for_symbol(connector, sym)
            con_id = int(getattr(contract, "conId", 0) or 0)
            if con_id <= 0:
                ib.qualifyContracts(contract)
                con_id = int(getattr(contract, "conId", 0) or 0)
            if con_id <= 0:
                continue
            filt = WshEventData(conId=con_id, filter='{"country":"All","watchlist":["Earnings"]}')
            events = ib.reqWshEventData(filt)
            ib.sleep(0.35)
            for ev in events or []:
                out.append({
                    "symbol": sym,
                    "con_id": con_id,
                    "event_type": str(getattr(ev, "eventType", "") or ""),
                    "event_date": str(getattr(ev, "eventDate", "") or ""),
                    "description": str(getattr(ev, "description", "") or "")[:200],
                })
        except Exception as exc:
            log.debug(f"reqWshEventData {sym}: {exc}")
    return out[:40]


def fetch_head_timestamps(ib, connector: "IBConnector", symbols: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for sym in symbols[:8]:
        sym = sym.upper()
        try:
            contract = _contract_for_symbol(connector, sym)
            ts = ib.reqHeadTimeStamp(contract, "TRADES", 1, 1)
            if ts:
                out[sym] = str(ts)
        except Exception as exc:
            log.debug(f"reqHeadTimeStamp {sym}: {exc}")
    return out


def fetch_market_rules(ib, con_ids: List[int]) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for cid in con_ids[:12]:
        if cid <= 0:
            continue
        try:
            rules = ib.reqMarketRule(cid)
            incs = []
            for r in rules or []:
                low = float(getattr(r, "lowEdge", 0) or 0)
                inc = float(getattr(r, "increment", 0) or 0)
                if inc > 0:
                    incs.append(round(inc, 6))
                elif low > 0:
                    incs.append(round(low, 6))
            if incs:
                out[str(cid)] = incs
        except Exception as exc:
            log.debug(f"reqMarketRule {cid}: {exc}")
    return out


def what_if_order(
    ib,
    connector: "IBConnector",
    symbol: str,
    quantity: int,
    *,
    limit_px: Optional[float] = None,
    action: str = "BUY",
) -> Dict[str, Any]:
    """IB whatIfOrder margin preview — no order placed."""
    from core.ib_client import LimitOrder, MarketOrder

    sym = (symbol or "").upper()
    out: Dict[str, Any] = {"symbol": sym, "qty": quantity, "ok": False}
    if quantity <= 0 or not sym:
        return out
    try:
        contract = _contract_for_symbol(connector, sym)
        if limit_px is not None and float(limit_px) > 0:
            order = LimitOrder(action, quantity, float(limit_px))
        else:
            order = MarketOrder(action, quantity)
        state = ib.whatIfOrder(contract, order)
        ib.sleep(0.2)
        if state is None:
            return out
        out.update({
            "ok": True,
            "init_margin_change": round(float(getattr(state, "initMarginChange", 0) or 0), 2),
            "maint_margin_change": round(float(getattr(state, "maintMarginChange", 0) or 0), 2),
            "equity_with_loan_change": round(float(getattr(state, "equityWithLoanChange", 0) or 0), 2),
            "commission": round(float(getattr(state, "commission", 0) or 0), 2),
            "init_margin_before": round(float(getattr(state, "initMarginBefore", 0) or 0), 2),
            "available_funds_implied": round(
                float(getattr(state, "equityWithLoanBefore", 0) or 0)
                - float(getattr(state, "initMarginBefore", 0) or 0),
                2,
            ),
        })
    except Exception as exc:
        log.debug(f"whatIfOrder {sym}: {exc}")
        out["error"] = str(exc)[:120]
    return out


def what_if_margin_allows(
    ib,
    connector: "IBConnector",
    symbol: str,
    quantity: int,
    *,
    limit_px: Optional[float] = None,
    cfg: Optional["BotConfig"] = None,
) -> tuple[bool, Dict[str, Any]]:
    """Gate entries when what-if init margin exceeds IB available funds."""
    if os.getenv("IB_WHATIF_MARGIN_GATE", "true").lower() not in ("1", "true", "yes"):
        return True, {"skipped": True}
    preview = what_if_order(ib, connector, symbol, quantity, limit_px=limit_px)
    if not preview.get("ok"):
        return True, preview
    try:
        from core.ib_truth import get_snapshot
        snap = get_snapshot()
        avail = float(snap.account.available_funds or snap.account.excess_liquidity or 0)
    except Exception:
        avail = float(preview.get("available_funds_implied", 0) or 0)
    need = float(preview.get("init_margin_change", 0) or 0)
    buffer = float(os.getenv("IB_WHATIF_MARGIN_BUFFER", "1.05"))
    allowed = need <= 0 or avail <= 0 or need * buffer <= avail
    preview["available_funds"] = round(avail, 2)
    preview["allowed"] = allowed
    return allowed, preview


def refresh_ib_extended(
    ib,
    cfg: Optional["BotConfig"] = None,
    connector: Optional["IBConnector"] = None,
    symbols: Optional[List[str]] = None,
    *,
    full: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Refresh extended IB data; returns merged cache."""
    if not ib_extended_enabled() or connector is None:
        return get_extended_cache()

    from core.ib_truth import get_snapshot

    snap = get_snapshot()
    syms = list(symbols or [])
    for p in snap.positions:
        if p.symbol and p.symbol not in syms:
            syms.append(p.symbol)
    if not syms:
        syms = ["SPY", "QQQ"]

    with _lock:
        cached = _load_cache()
        ttl = _FULL_TTL if full else _LIGHT_TTL
        last = float(cached.get("refreshed_at", 0) or 0)
        if not force and last > 0 and (time.time() - last) < ttl:
            return cached

    account = _primary_account(ib)
    bundle = IBExtendedBundle(refreshed_at=time.time(), refresh_mode="full" if full else "light")

    try:
        bundle.account_pnl = fetch_account_pnl(ib, account)
    except Exception as exc:
        log.debug(f"account_pnl: {exc}")

    try:
        positions = list(ib.positions())
        bundle.position_pnl = fetch_position_pnl_single(ib, account, positions)
    except Exception as exc:
        log.debug(f"position_pnl: {exc}")

    try:
        bundle.contract_details = fetch_contract_details(ib, connector, syms)
    except Exception as exc:
        log.debug(f"contract_details: {exc}")

    con_ids = [int(d.get("con_id", 0) or 0) for d in bundle.contract_details.values()]
    for p in snap.positions:
        if p.con_id and p.con_id not in con_ids:
            con_ids.append(p.con_id)
    if market_rules_enabled(cfg):
        try:
            bundle.market_rules = fetch_market_rules(ib, con_ids)
        except Exception as exc:
            log.debug(f"market_rules: {exc}")

    try:
        bundle.quote_snapshots = fetch_quote_snapshots(ib, connector, syms)
    except Exception as exc:
        log.debug(f"quote_snapshots: {exc}")

    try:
        bundle.account_summary = fetch_account_summary(ib, account)
    except Exception as exc:
        log.debug(f"account_summary: {exc}")

    if full or os.getenv("IB_EXTENDED_RTH_FULL", "false").lower() in ("1", "true", "yes"):
        try:
            bundle.head_timestamps = fetch_head_timestamps(ib, connector, syms)
        except Exception as exc:
            log.debug(f"head_timestamps: {exc}")
        try:
            bundle.fundamentals = fetch_fundamentals(ib, connector, syms)
        except Exception as exc:
            log.debug(f"fundamentals: {exc}")
        try:
            bundle.news_bulletins = fetch_news_bulletins(ib)
        except Exception as exc:
            log.debug(f"news_bulletins: {exc}")
        try:
            bundle.news_historical = fetch_historical_news(ib, connector, syms)
        except Exception as exc:
            log.debug(f"news_historical: {exc}")
        try:
            bundle.wsh_events = fetch_wsh_events(ib, connector, syms)
        except Exception as exc:
            log.debug(f"wsh_events: {exc}")
        try:
            bundle.completed_orders = fetch_completed_orders(ib)
        except Exception as exc:
            log.debug(f"completed_orders: {exc}")
        try:
            bundle.news_providers = fetch_news_providers_list(ib)
        except Exception as exc:
            log.debug(f"news_providers: {exc}")

    data = {
        "refreshed_at": bundle.refreshed_at,
        "refresh_mode": bundle.refresh_mode,
        "account_pnl": bundle.account_pnl,
        "position_pnl": bundle.position_pnl,
        "contract_details": bundle.contract_details,
        "fundamentals": bundle.fundamentals,
        "news_bulletins": bundle.news_bulletins,
        "news_historical": bundle.news_historical,
        "wsh_events": bundle.wsh_events,
        "head_timestamps": bundle.head_timestamps,
        "market_rules": bundle.market_rules,
        "quote_snapshots": bundle.quote_snapshots,
        "account_summary": bundle.account_summary,
        "completed_orders": bundle.completed_orders,
        "news_providers": bundle.news_providers,
    }
    with _lock:
        _save_cache(data)
    log.info(
        f"IB extended refresh ({bundle.refresh_mode}): "
        f"pnl={len(bundle.position_pnl)} contracts={len(bundle.contract_details)} "
        f"news={len(bundle.news_bulletins)} wsh={len(bundle.wsh_events)}"
    )
    return data


def extended_ai_context() -> Dict[str, Any]:
    """Slice of extended cache for Halim/council."""
    c = get_extended_cache()
    if not c.get("refreshed_at"):
        return {"ib_extended": False}
    return {
        "ib_extended": True,
        "ib_extended_refreshed_at": c.get("refreshed_at"),
        "ib_account_pnl_stream": c.get("account_pnl", {}),
        "ib_position_pnl_single": c.get("position_pnl", {}),
        "ib_contract_details": c.get("contract_details", {}),
        "ib_fundamentals": c.get("fundamentals", {}),
        "ib_news_bulletins": (c.get("news_bulletins") or [])[:8],
        "ib_news_headlines": {
            k: v[:3] for k, v in (c.get("news_historical") or {}).items()
        },
        "ib_wsh_events": (c.get("wsh_events") or [])[:12],
        "ib_head_timestamps": c.get("head_timestamps", {}),
        "ib_market_rules": c.get("market_rules", {}),
        "ib_quote_snapshots": c.get("quote_snapshots", {}),
        "ib_account_summary": c.get("account_summary", {}),
        "ib_completed_orders": (c.get("completed_orders") or [])[:10],
        "ib_news_providers": c.get("news_providers", []),
    }
