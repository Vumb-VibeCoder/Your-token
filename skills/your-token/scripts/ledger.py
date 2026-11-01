#!/usr/bin/env python3
"""your-token ledger: estimates claude.ai usage and tracks exact Claude API spend.

Two clocks:
  USAGE    claude.ai: weighted tokens in the rolling 5h window vs a budget.
           API: USD spend in a period (session | day | month) vs an optional cap.
  CONTEXT  how full this conversation is. Only a new chat/session resets it.

The ledger creates itself on first use (Free plan, Sonnet 5.5), no questions asked.
Env:  YT_LEDGER  ledger file (default ~/.your-token/ledger.json, same from any directory)
      YT_NOW     fake clock, for tests
Global flag: --ledger FILE, placed before the command.
Commands: init plan add turn api-usage status predict window calibrate export restore reset
Output is English on purpose. Claude relays it in the user's language.
"""
import argparse, json, math, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import chars_to_tokens, detect_lang, est_tokens  # noqa: E402

PLANS_PATH = Path(__file__).resolve().parent.parent / "plans.json"
INPUT_TYPES = {"system", "user", "tool_result"}
OUTPUT_TYPES = {"assistant_text", "tool_call", "thinking", "created_file"}
BLOCK_RE = re.compile(r"```your-token-ledger\s*(\{.*?\})\s*```", re.S)
PERIODS = {"day": 86400, "month": 30 * 86400}
DEFAULT_PLAN = "claude_free"
MAX_TURNS_KEPT = 200


def plans():
    return json.loads(PLANS_PATH.read_text(encoding="utf-8"))


def now():
    return float(os.environ.get("YT_NOW", time.time()))


def fmt_dur(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def spath():
    env = os.environ.get("YT_LEDGER")
    return Path(env) if env else Path.home() / ".your-token" / "ledger.json"


def save(s):
    p = spath()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def blank_pending():
    return {"system": 0, "user": 0, "tool_result": 0, "assistant_text": 0,
            "tool_call": 0, "thinking": 0, "created_file": 0}


def blank_totals():
    return {"input_billed": 0, "output": 0, "weighted": 0}


def models_state(p):
    return {k: {"out_ratio": round(v["price_out"] / v["price_in"], 3), "usage_weight": v["usage_weight"],
                "price_in": v["price_in"], "price_out": v["price_out"]} for k, v in p["models"].items()}


def sync_budget(s):
    s["budget_tokens"] = int(round(s["free_base"] * s["multiplier"]))


def apply_plan(s, plan_key, p):
    cfg = p["plans"][plan_key]
    s.update({"plan": plan_key, "mode": "api" if plan_key == "api" else "claude_ai",
              "multiplier": cfg["multiplier"], "window_hours": cfg["window_hours"],
              "warn_at": cfg["warn_at"], "handoff_at": cfg["handoff_at"],
              "cache_discount": cfg["cache_discount"], "plan_models": cfg["models"]})
    sync_budget(s)


def new_state(plan_key=DEFAULT_PLAN, model=None, budget=None, budget_usd=None, period="session"):
    p = plans()
    if plan_key not in p["plans"]:
        sys.exit(f"Unknown plan. Choose from: {list(p['plans'])}")
    cw = p["context_window"]
    s = {"free_base": p["free_budget_tokens"], "cache_ttl_sec": p["cache_ttl_sec"],
         "context_limit": cw["limit"], "context_warn": cw["warn_at"], "context_handoff": cw["handoff_at"],
         "marginal_warn": cw["marginal_warn"], "models": models_state(p),
         "prices_as_of": p.get("prices_as_of"),
         "context_tokens": 0, "window_start": None, "windows_done": 0, "last_turn_time": None,
         "totals": blank_totals(), "pending": blank_pending(), "turns": [], "calibration": [],
         "spend_usd": 0.0, "budget_usd": budget_usd, "period": period, "period_start": None,
         "limits": {}, "events": [], "calls": 0}
    apply_plan(s, plan_key, p)
    if budget:
        s["free_base"] = budget / s["multiplier"]
        sync_budget(s)
    s["model"] = resolve_model(s, model or p["plans"][plan_key]["default_model"])
    return s


def migrate(s):
    """Add keys that older ledgers don't have, so loading one doesn't crash."""
    base = new_state()
    for k, v in base.items():
        s.setdefault(k, v)
    for k, v in base["models"].items():
        m = s["models"].setdefault(k, v)
        for f in ("price_in", "price_out"):
            m.setdefault(f, v[f])
    if "mode" not in s:
        s["mode"] = "api" if s.get("plan") == "api" else "claude_ai"
    return s


def load():
    p = spath()
    if not p.exists():
        s = new_state()
        save(s)
        print("(Ledger created: assuming Free plan, Sonnet 5.5. Change: `plan --set claude_pro|claude_max5|claude_max20` "
              "or `init --api`.)")
        return s
    try:
        return migrate(json.loads(p.read_text(encoding="utf-8")))
    except ValueError:
        sys.exit(f"Ledger file {p} is not valid JSON. Fix it, or delete it to start over.")


def resolve_effort(v):
    sc = plans()["effort_scale"]
    v = str(v).strip().lower()
    if v in sc:
        return v, sc[v]
    if v in sc.values():
        return next((k for k, n in sc.items() if n == v), "?"), v
    sys.exit(f"Invalid effort: {v}. Use {list(sc)} or {list(sc.values())}")


def _norm(x):
    return str(x).lower().replace(" ", "-").replace("_", "-").replace(".", "-")


def resolve_model(s, name):
    """'Claude Opus 5.5' / 'claude-opus-5-5' -> 'opus-5.5' (longest matching key wins)."""
    low = _norm(name)
    cands = [k for k in s["models"] if _norm(k) in low]
    if not cands:
        sys.exit(f"Unknown model '{name}'. Known: {list(s['models'])}. Add one with: calibrate --model-weight {low}=1.0")
    return max(cands, key=len)


def model_warning(s):
    pm = s.get("plan_models") or []
    fam = s["model"].split("-")[0]
    if pm and not any(m.split("-")[0] == fam for m in pm):
        print(f"(!) Model {s['model']} is not normally in plan {s['plan']} (has: {', '.join(pm)}). Check model/plan.")


# ---------- time windows ----------
def check_window(s):
    ws = s.get("window_start")
    if ws is not None and now() - ws >= s["window_hours"] * 3600:
        s["totals"] = blank_totals()
        s["window_start"] = None
        s["windows_done"] = s.get("windows_done", 0) + 1
        print(f"** NEW {s['window_hours']:g}H WINDOW: usage counter reset to 0 "
              f"(this chat's context is unchanged). Tell the user in one line.")
        return True
    return False


def check_period(s):
    secs = PERIODS.get(s["period"])
    ps = s.get("period_start")
    if secs and ps is not None and now() - ps >= secs:
        s["spend_usd"] = 0.0
        s["period_start"] = None
        print(f"** NEW {'24H' if s['period'] == 'day' else '30D'} PERIOD: spend counter reset to 0. "
              f"Tell the user in one line.")
        return True
    return False


def tick(s):
    """Reset an expired window/period and start the clock if it has not started."""
    if s["mode"] == "api":
        r = check_period(s)
        if s["period_start"] is None:
            s["period_start"] = now()
    else:
        r = check_window(s)
        if s["window_start"] is None:
            s["window_start"] = now()
    return r


def window_line(s):
    ws = s.get("window_start")
    if ws is None:
        return "Window: not started (ask the user once how long ago their 5h window started; set with `window --started-ago-min N` or `window --fresh` if idle 5h+)"
    return f"Window {s['window_hours']:g}h: {fmt_dur(ws + s['window_hours'] * 3600 - now())} until reset"


# ---------- cost model ----------
def compute(s, pend, rounds, cd=None):
    """(input_billed, output_tokens) of one turn BEFORE the model weight."""
    cd = s["cache_discount"] if cd is None else cd
    ctx = s["context_tokens"] + pend["system"] + pend["user"]
    repeat = max(0, rounds - 1)
    inp = ctx + repeat * ctx * (1 - cd)           # every tool round re-reads the whole context
    inp += pend["tool_result"] * (1 + repeat / 2 * (1 - cd))
    out = sum(pend[k] for k in OUTPUT_TYPES)
    return inp, out


def turn_cost(s, inp, out):
    m = s["models"][s["model"]]
    return (inp + out * m["out_ratio"]) * m["usage_weight"]


def usd_estimate(s, inp, out):
    m = s["models"][s["model"]]
    return (inp * m.get("price_in", 0) + out * m.get("price_out", 0)) / 1e6


def api_cost(s, a):
    m = s["models"][s["model"]]
    ap = plans()["api_pricing"]
    pin, pout = m.get("price_in", 0), m.get("price_out", 0)
    c = (a.input * pin + a.cache_read * pin * ap["cache_read"] + a.cache_write * pin * ap["cache_write_5m"]
         + a.cache_write_1h * pin * ap["cache_write_1h"] + a.output * pout)
    return c * (ap["batch"] if a.batch else 1) / 1e6


def level_of(pct, warn, hand):
    return "HANDOFF" if pct >= hand else ("WARN" if pct >= warn else "OK")


def levels(s):
    if s["mode"] == "api":
        b, used, none = s.get("budget_usd"), s["spend_usd"], "NO_CAP"
    else:
        b, used, none = s["budget_tokens"], s["totals"]["weighted"], "NO_BUDGET"
    u_pct = (used / b) if b else None
    u_lv = level_of(u_pct, s["warn_at"], s["handoff_at"]) if b else none
    c_pct = s["context_tokens"] / s["context_limit"]
    return u_pct, u_lv, c_pct, level_of(c_pct, s["context_warn"], s["context_handoff"])


# ---------- marginal cost & forecast (claude.ai) ----------
def recent_avg(s, k=3):
    tt = s["turns"][-k:]
    if not tt:
        return {"user": 200, "tool_result": 0, "out": 500, "calls": 0}
    n = len(tt)
    return {"user": sum(x.get("user", 200) for x in tt) / n,
            "tool_result": sum(x.get("tool_result", 0) for x in tt) / n,
            "out": sum(x["output"] for x in tt) / n,
            "calls": sum(x["tool_calls"] for x in tt) / n}


def sim_cost(s, ctx, av):
    cd = s["cache_discount"]
    rep = max(0, av["calls"])
    c = ctx + av["user"]
    inp = c + rep * c * (1 - cd) + av["tool_result"] * (1 + rep / 2 * (1 - cd))
    return turn_cost(s, inp, av["out"])


def runway(s, ctx0, used0, av):
    limit = s["budget_tokens"] * s["handoff_at"]
    used, ctx, n = used0, ctx0, 0
    while n < 500:
        c = sim_cost(s, ctx, av)
        if used + c > limit:
            break
        used += c
        ctx += av["user"] + av["tool_result"] + av["out"]
        n += 1
    return n


def marginal_info(s):
    b = s["budget_tokens"]
    if s["mode"] == "api" or not b:
        return None
    av = recent_avg(s)
    nxt = sim_cost(s, s["context_tokens"], av)
    used = s["totals"]["weighted"]
    n_here, n_new = runway(s, s["context_tokens"], used, av), runway(s, 1500, used, av)
    pct = nxt / b
    return {"next": nxt, "pct": pct, "n_here": n_here, "n_new": n_new,
            "advise": pct >= s.get("marginal_warn", 0.10) and n_new > n_here}


# ---------- rate limits (API) ----------
def rate_now(s):
    t = now()
    s["events"] = [e for e in s["events"] if t - e["t"] < 60]
    return len(s["events"]), sum(e["in"] for e in s["events"]), sum(e["out"] for e in s["events"])


def rate_line(s):
    lim = s.get("limits") or {}
    if not lim:
        return None, False
    n, i, o = rate_now(s)
    parts, hot = [], False
    for name, val, key in (("req", n, "rpm"), ("in", i, "itpm"), ("out", o, "otpm")):
        if lim.get(key):
            pct = val / lim[key]
            parts.append(f"{name} {val}/{lim[key]} ({pct * 100:.0f}%)")
            hot = hot or pct >= 0.8
    return ("RATE (last 60s): " + ", ".join(parts), hot) if parts else (None, False)


def price_age_days(s):
    try:
        return (now() - time.mktime(time.strptime(s.get("prices_as_of") or "", "%Y-%m-%d"))) / 86400
    except ValueError:
        return None


# ---------- reporting ----------
def print_tips():
    print("SAVING TIPS:")
    print(" - Put several questions in one message. Every message re-reads the whole context.")
    print(" - For simple jobs, use a smaller model (Haiku) or turn extended thinking off.")
    print(" - Don't pause more than 5 min in the middle of heavy work. The cache expires and the next turn is billed in full.")
    print(" - For big files, read only the part you need (skim), or put them in a Project instead of pasting them again.")


def print_status(s):
    u_pct, u_lv, c_pct, c_lv = levels(s)
    api = s["mode"] == "api"
    m = s["models"][s["model"]]
    if api:
        print(f"mode=api model={s['model']} calls={s['calls']} period={s['period']}")
        cap = f" of ${s['budget_usd']:g} cap = {u_pct * 100:.0f}% -> {u_lv}" if u_pct is not None else " (no cap set)"
        print(f"SPEND  : ${s['spend_usd']:.4f}{cap}")
        ps, secs = s.get("period_start"), PERIODS.get(s["period"])
        if ps and secs:
            print(f"Period : {fmt_dur(ps + secs - now())} until reset")
        rl, _ = rate_line(s)
        if rl:
            print(rl)
        age = price_age_days(s)
        if age is not None and age > 90:
            print(f"(!) List prices dated {s['prices_as_of']} (>90 days). Verify at {plans().get('prices_url', 'the pricing page')} "
                  f"and update with `calibrate --price NAME=IN/OUT` (USD per MTok).")
    else:
        t = s["totals"]
        print(f"plan={s['plan']} (x{s['multiplier']:g} Free) model={s['model']} "
              f"[weight x{m['usage_weight']:g}, output = {m['out_ratio']:g}x input] turns={s['calls']}")
        print(window_line(s))
        print(f"USAGE  : input_billed~{int(t['input_billed'])} output~{int(t['output'])} weighted~{int(t['weighted'])}")
        if u_pct is None:
            print("         No budget yet: `calibrate --budget N`, or `calibrate --hit-limit` when you get limited.")
        else:
            print(f"         {u_pct * 100:.0f}% of window budget ({s['budget_tokens']}) -> {u_lv}")
    print(f"CONTEXT: ~{int(s['context_tokens'])}/{s['context_limit']} = {c_pct * 100:.0f}% -> {c_lv}")
    mi = marginal_info(s)
    if mi:
        print(f"MARGINAL COST: next message ~{int(mi['next'])} ({mi['pct'] * 100:.0f}% of budget), includes re-reading the whole context")
        print(f"FORECAST: ~{mi['n_here']} turns left before handoff in this chat; "
              f"~{mi['n_new']} in a fresh chat from HANDOFF (same window)")
        if mi["advise"]:
            print("=> OPEN A NEW CHAT: the context is so large that each message mostly pays to re-read it. "
                  "Write HANDOFF.md now (even if usage is low) and continue in a new chat.")
    if c_lv == "HANDOFF":
        print("=> CONTEXT nearly full: write HANDOFF.md, send the files, user opens a NEW CHAT "
              "(a window reset does not fix this).")
    if u_lv == "HANDOFF":
        if api:
            print("=> SPEND near the cap: stop new work at a clean point, write HANDOFF.md; the user can raise the cap "
                  "or wait for the period reset.")
        else:
            ws = s.get("window_start")
            extra = f" Window resets in ~{fmt_dur(ws + s['window_hours'] * 3600 - now())}." if ws else ""
            print("=> USAGE nearly out: stop new work at a clean point, write HANDOFF.md and send the files now." + extra)
    if "WARN" in (u_lv, c_lv) and "HANDOFF" not in (u_lv, c_lv):
        print("=> Heads-up: do the most important work first, and skip deep reads and high effort where you can.")
    if {"WARN", "HANDOFF"} & {u_lv, c_lv} or (mi and mi["advise"]):
        print_tips()


def brief(s):
    u_pct, u_lv, c_pct, c_lv = levels(s)
    if u_pct is not None:
        u = f"{u_pct * 100:.0f}% {u_lv}"
    else:
        u = f"${s['spend_usd']:.4f} {u_lv}" if s["mode"] == "api" else u_lv
    return f"USAGE {u} | CONTEXT {c_pct * 100:.0f}% {c_lv}"


def report(s, reset=False, verbose=False):
    """One short line when everything is OK; the full status only when something needs attention."""
    u_pct, u_lv, c_pct, c_lv = levels(s)
    mi = marginal_info(s)
    _, hot = rate_line(s)
    if verbose or reset or hot or {"WARN", "HANDOFF"} & {u_lv, c_lv} or (mi and mi["advise"]):
        print_status(s)
    else:
        print(brief(s))


# ---------- helpers for turns ----------
def fill_pending(s, a, prof):
    pend = dict(s["pending"])
    lang = a.lang or "mixed"
    if a.user_chars:
        pend["user"] += chars_to_tokens(a.user_chars, lang)
    if a.result_tokens:
        pend["tool_result"] += a.result_tokens
    if a.out_chars:
        pend["assistant_text"] += chars_to_tokens(a.out_chars, lang)
    if a.created_chars:
        pend["created_file"] += chars_to_tokens(a.created_chars, "code" if a.lang == "code" else lang)
    if a.thinking_tokens is not None:
        pend["thinking"] += a.thinking_tokens
    else:
        pend["thinking"] += math.ceil(pend["assistant_text"] * prof["thinking_ratio"])
    if pend["tool_call"] == 0 and a.tool_calls:
        pend["tool_call"] = a.tool_calls * plans()["tool_call_tokens"]
    return pend


def gap_seconds(s, a):
    if a.gap_min is not None:
        return a.gap_min * 60
    if s.get("last_turn_time") is not None:
        return now() - s["last_turn_time"]
    return 0


# ---------- commands ----------
def cmd_init(a):
    plan = "api" if a.api else (a.plan or DEFAULT_PLAN)
    s = new_state(plan, a.model, a.budget, a.budget_usd, a.period or "session")
    save(s)
    if s["mode"] == "api":
        cap = f"${a.budget_usd:g}" if a.budget_usd else "none"
        print(f"OK init api model={s['model']} cap={cap} period={s['period']}")
    else:
        print(f"OK init plan={plan} (x{s['multiplier']:g}) model={s['model']} "
              f"window budget~{s['budget_tokens']} (a guess, calibrate it)")
    model_warning(s)


def cmd_plan(a):
    s = load()
    p = plans()
    if a.set not in p["plans"]:
        sys.exit(f"Unknown plan. Choose from: {list(p['plans'])}")
    apply_plan(s, a.set, p)
    if a.model:
        s["model"] = resolve_model(s, a.model)
    save(s)
    print(f"Switched to {a.set} (x{s['multiplier']:g} Free): window budget~{s['budget_tokens']}")
    model_warning(s)


def tokens_for_add(a):
    if a.tokens:
        return a.tokens
    if a.file:
        try:
            text = Path(a.file).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            sys.exit(f"Can't read {a.file}: {e.strerror or e}")
        lines = text.splitlines()
        if a.lines:
            x, y = a.lines.split("-")
            lines = lines[int(x) - 1:int(y)]
        elif a.read_mode == "skim":
            lines = lines[:80]
        text = "\n".join(lines)
        return est_tokens(text, a.lang or detect_lang(text, a.file)) * max(1, a.times)
    if a.chars:
        return chars_to_tokens(a.chars, a.lang or "mixed")
    sys.exit("Need --tokens, --chars or --file")


def cmd_add(a):
    s = load()
    t = tokens_for_add(a)
    s["pending"][a.type] += t
    save(s)
    print(f"+{t} tokens ({a.type}, {'input' if a.type in INPUT_TYPES else 'output'}) added to the open turn")


def cmd_turn(a):
    s = load()
    reset = tick(s)
    if a.model:
        s["model"] = resolve_model(s, a.model)
        model_warning(s)
    raw, name = resolve_effort(a.effort)
    prof = plans()["effort_profiles"][name]
    if a.turns < 1:
        sys.exit("--turns must be at least 1.")
    n = a.turns
    pend = fill_pending(s, a, prof)
    calls = (a.tool_calls or 0) / n
    if n > 1:                                      # one call logging n similar turns
        pend = {k: v / n for k, v in pend.items()}
    gap = gap_seconds(s, a)
    expired = gap > s["cache_ttl_sec"]
    tot_in = tot_out = tot_cost = tot_usd = 0.0
    for i in range(n):
        cd = 0.0 if (expired and i == 0) else s["cache_discount"]   # expired cache -> re-reads billed in full
        inp, out = compute(s, pend, calls + 1, cd)
        cost = turn_cost(s, inp, out)
        usd = usd_estimate(s, inp, out)
        s["totals"]["input_billed"] += inp
        s["totals"]["output"] += out
        s["totals"]["weighted"] += cost
        if s["mode"] == "api":
            s["spend_usd"] += usd
        s["context_tokens"] += pend["system"] + pend["user"] + pend["tool_result"] + out
        s["calls"] += 1
        s["turns"].append({"n": s["calls"], "model": s["model"], "effort": name, "effort_raw": raw,
                           "tool_calls": round(calls), "input_billed": int(inp), "output": int(out),
                           "user": int(pend["user"]), "tool_result": int(pend["tool_result"]),
                           "cache_expired": bool(expired and i == 0), "weighted": int(cost)})
        tot_in, tot_out, tot_cost, tot_usd = tot_in + inp, tot_out + out, tot_cost + cost, tot_usd + usd
    s["turns"] = s["turns"][-MAX_TURNS_KEPT:]
    s["pending"] = blank_pending()
    s["last_turn_time"] = now()
    save(s)
    extra = f"+${tot_usd:.4f} (estimate)" if s["mode"] == "api" else f"+weighted {int(tot_cost)}"
    print(f"turn {s['calls']}{f' (x{n})' if n > 1 else ''} [{s['model']}/{name}] +in {int(tot_in)} +out {int(tot_out)} {extra}")
    if expired and s["cache_discount"] > 0:
        print(f"(paused {gap / 60:.0f} min > {s['cache_ttl_sec'] / 60:.0f}: cache expired, this turn billed in full)")
    report(s, reset, a.verbose)


def cmd_api_usage(a):
    s = load()
    if s["mode"] != "api":
        sys.exit("Ledger is in claude.ai mode. Run `init --api [--budget-usd N]` first (this starts a fresh ledger).")
    reset = tick(s)
    if a.model:
        s["model"] = resolve_model(s, a.model)
    cost = api_cost(s, a)
    s["spend_usd"] += cost
    billed_in = a.input + a.cache_write + a.cache_write_1h
    s["totals"]["input_billed"] += billed_in
    s["totals"]["output"] += a.output
    s["context_tokens"] = a.input + a.cache_read + a.cache_write + a.cache_write_1h + a.output
    s["calls"] += 1
    s["events"].append({"t": now(), "in": billed_in, "out": a.output})
    s["turns"].append({"n": s["calls"], "model": s["model"], "tool_calls": 0, "input_billed": billed_in,
                       "output": a.output, "user": 0, "tool_result": 0, "cache_expired": False,
                       "weighted": 0, "usd": round(cost, 6)})
    s["turns"] = s["turns"][-MAX_TURNS_KEPT:]
    s["last_turn_time"] = now()
    save(s)
    print(f"call {s['calls']} [{s['model']}] +${cost:.6f}")
    report(s, reset, a.verbose)


def cmd_status(a):
    s = load()
    if (check_period(s) if s["mode"] == "api" else check_window(s)):
        save(s)
    print_status(s)


def cmd_predict(a):
    s = load()
    ws_expired = s.get("window_start") is not None and now() - s["window_start"] >= s["window_hours"] * 3600
    base_w = 0 if ws_expired else s["totals"]["weighted"]
    if a.model:
        s = dict(s)
        s["model"] = resolve_model(s, a.model)
    raw, name = resolve_effort(a.effort)
    prof = plans()["effort_profiles"][name]
    if a.tool_calls is None:
        a.tool_calls = prof["expected_tool_calls"]
    pend = fill_pending(s, a, prof)
    expired = (a.gap_min or 0) * 60 > s["cache_ttl_sec"]
    inp, out = compute(s, pend, a.tool_calls + 1, 0.0 if expired else None)
    tag = f"[{s['model']}/{name}, {a.tool_calls} tools{', cache expired' if expired else ''}]"
    if s["mode"] == "api":
        usd = usd_estimate(s, inp, out)
        print(f"Prediction {tag}: ~${usd:.4f}")
        if s.get("budget_usd"):
            print(f"SPEND after this job: ~{(s['spend_usd'] + usd) / s['budget_usd'] * 100:.0f}% of cap")
    else:
        cost = turn_cost(s, inp, out)
        print(f"Prediction {tag}: ~{int(cost)} weighted")
        b = s["budget_tokens"]
        if b:
            after = (base_w + cost) / b
            print(f"USAGE after this job: ~{after * 100:.0f}% of window budget")
            if after >= s["handoff_at"]:
                print("=> Over the usage threshold: lower effort/model, split the job, or hand off BEFORE starting.")
        else:
            print("(no budget to compare usage against)")
    ctx_after = (s["context_tokens"] + pend["user"] + pend["tool_result"] + out) / s["context_limit"]
    print(f"CONTEXT after this job: ~{ctx_after * 100:.0f}%")
    if ctx_after >= s["context_handoff"]:
        print("=> Over the context threshold: hand off / open a new chat before starting.")


def cmd_window(a):
    s = load()
    if a.fresh:                                   # user was idle >= window length: a new window starts now
        s["totals"] = blank_totals()
        s["window_start"] = now()
    elif a.started_ago_min is not None:
        s["window_start"] = now() - a.started_ago_min * 60
    if a.hours:
        s["window_hours"] = a.hours
    save(s)
    print(window_line(s))


def cmd_calibrate(a):
    for flag, val in (("--budget", a.budget), ("--budget-usd", a.budget_usd)):
        if val is not None and val <= 0:
            sys.exit(f"{flag} must be greater than 0.")
    s = load()
    if a.budget:
        s["free_base"] = a.budget / s["multiplier"]
        sync_budget(s)
        print(f"Window budget set to {s['budget_tokens']} (Free-equivalent ~{int(s['free_base'])})")
    if a.budget_usd:
        s["budget_usd"] = a.budget_usd
        print(f"Spend cap set to ${a.budget_usd:g}")
    if a.context_limit:
        s["context_limit"] = a.context_limit
        print(f"context_limit set to {a.context_limit}")
    if a.cache_discount is not None:
        s["cache_discount"] = a.cache_discount
        print(f"cache_discount set to {a.cache_discount}")
    for kv in a.model_weight or []:
        k, v = kv.split("=")
        k = k.lower()
        s["models"].setdefault(k, {"out_ratio": 5.0, "usage_weight": 1.0})["usage_weight"] = float(v)
        print(f"usage_weight[{k}] = {float(v)}")
    for kv in a.price or []:
        k, v = kv.split("=")
        pin, pout = (float(x) for x in v.split("/"))
        m = s["models"].setdefault(k.lower(), {"usage_weight": 1.0})
        m.update({"price_in": pin, "price_out": pout, "out_ratio": round(pout / pin, 3)})
        s["prices_as_of"] = time.strftime("%Y-%m-%d", time.localtime(now()))
        print(f"price[{k.lower()}] = ${pin:g} in / ${pout:g} out per MTok (as of {s['prices_as_of']})")
    lim = s.setdefault("limits", {})
    for key in ("rpm", "itpm", "otpm"):
        if getattr(a, key):
            lim[key] = getattr(a, key)
            print(f"rate limit {key} = {lim[key]}")
    if a.hit_rate_limit:
        n, i, o = rate_now(s)
        for key, val in (("rpm", n), ("itpm", i), ("otpm", o)):
            if val > 0:
                lim[key] = val
        print(f"Rate limit hit: recorded last-60s rates as limits {lim}. Back off and retry.")
    if a.hit_limit:
        if s["mode"] == "api":
            if s["spend_usd"] <= 0:
                sys.exit("Spend is 0, nothing to calibrate. Run hit-limit right when the cap is hit, before any reset.")
            s["budget_usd"] = round(s["spend_usd"], 6)
            print(f"Cap hit at ${s['budget_usd']:g}. Recorded as the spend cap.")
        else:
            obs = s["totals"]["weighted"]
            if obs <= 0:
                sys.exit("Usage is 0 (no turns logged in this window), nothing to calibrate. "
                         "Run hit-limit right when you are limited, before the window resets.")
            s["calibration"].append(obs / s["multiplier"])      # normalized to Free units across plans
            s["free_base"] = sum(s["calibration"]) / len(s["calibration"])
            sync_budget(s)
            print(f"Limit hit at weighted~{int(obs)}. New budget = {s['budget_tokens']} "
                  f"(mean of {len(s['calibration'])} observation(s), normalized by plan)")
    save(s)


def cmd_export(a):
    s = load()
    s["pending"] = blank_pending()
    block = "```your-token-ledger\n" + json.dumps(s, ensure_ascii=False) + "\n```\n"
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write("\n## Token ledger (your-token)\n" + block)
        print(f"Ledger appended to {a.out}")
    else:
        print(block)


def cmd_restore(a):
    try:
        text = Path(a.file).read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"Can't read {a.file}: {e.strerror or e}")
    m = BLOCK_RE.search(text)
    if not m:
        sys.exit("No your-token-ledger block found in the file.")
    try:
        s = migrate(json.loads(m.group(1)))
    except ValueError:
        sys.exit("The ledger block in that file is damaged (not valid JSON).")
    if s["mode"] == "api":
        check_period(s)
    else:
        check_window(s)
    s["context_tokens"] = est_tokens(text)
    s["pending"] = blank_pending()
    s["last_turn_time"] = None
    save(s)
    print("Ledger restored (plan, model, calibration kept). Context recomputed for the NEW chat (HANDOFF only).")
    print_status(s)


def cmd_reset(a):
    s = load()
    s["totals"] = blank_totals()
    s["window_start"] = None
    s["pending"] = blank_pending()
    s["turns"] = []
    s["last_turn_time"] = None
    s["spend_usd"] = 0.0
    s["period_start"] = None
    s["events"] = []
    if a.new_chat:
        s["context_tokens"] = 0
    save(s)
    print("Usage reset" + (" and context (new chat)." if a.new_chat else " (context kept).") +
          " Budget, weights and calibration kept.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", help="ledger file (same as env YT_LEDGER); put it BEFORE the command")
    sub = ap.add_subparsers(dest="cmd", required=True)
    langs = ["vi", "en", "code", "mixed"]

    p = sub.add_parser("init", help="optional: the ledger auto-creates with Free + Sonnet 5.5")
    p.add_argument("--plan", help="claude_free|claude_pro|claude_max5|claude_max20|custom")
    p.add_argument("--api", action="store_true", help="Claude API mode: dollar spend instead of window tokens")
    p.add_argument("--model", help="e.g. 'Claude Opus 5.5' / opus-5.5 / claude-sonnet-5-5")
    p.add_argument("--budget", type=int, help="override the claude.ai window budget")
    p.add_argument("--budget-usd", type=float, help="API spend cap in USD (optional)")
    p.add_argument("--period", choices=["session", "day", "month"], help="API cap period (default session)")
    p.set_defaults(f=cmd_init)

    p = sub.add_parser("plan"); p.add_argument("--set", required=True); p.add_argument("--model")
    p.set_defaults(f=cmd_plan)

    p = sub.add_parser("add")
    p.add_argument("--type", required=True, choices=sorted(INPUT_TYPES | OUTPUT_TYPES))
    p.add_argument("--tokens", type=int); p.add_argument("--chars", type=int)
    p.add_argument("--file"); p.add_argument("--lines", help="e.g. 1-80")
    p.add_argument("--read-mode", choices=["skim", "normal", "deep"], default="normal")
    p.add_argument("--times", type=int, default=1, help="number of re-reads (deep)")
    p.add_argument("--lang", choices=langs); p.set_defaults(f=cmd_add)

    def turn_args(p):
        p.add_argument("--effort", default="5", help="value of the system reasoning_effort tag: 4=low, 5=medium, 10=high, 30=extra, max=max (or a name); default 5")
        p.add_argument("--model", help="pass on the turn the model changes")
        p.add_argument("--tool-calls", type=int, default=None)
        p.add_argument("--user-chars", type=int); p.add_argument("--out-chars", type=int)
        p.add_argument("--created-chars", type=int); p.add_argument("--result-tokens", type=int)
        p.add_argument("--thinking-tokens", type=int); p.add_argument("--lang", choices=langs)
        p.add_argument("--gap-min", type=float, help="minutes paused since the last turn (default: measured)")

    p = sub.add_parser("turn", help="log a turn (or --turns N similar turns in one call)")
    turn_args(p)
    p.add_argument("--turns", type=int, default=1, help="log N similar turns at once; totals are split evenly")
    p.add_argument("--verbose", action="store_true", help="always print the full status")
    p.set_defaults(f=cmd_turn)
    p = sub.add_parser("predict"); turn_args(p); p.set_defaults(f=cmd_predict)

    p = sub.add_parser("api-usage", help="log one API response's exact usage object")
    p.add_argument("--input", type=int, required=True); p.add_argument("--output", type=int, required=True)
    p.add_argument("--cache-read", type=int, default=0); p.add_argument("--cache-write", type=int, default=0)
    p.add_argument("--cache-write-1h", type=int, default=0)
    p.add_argument("--batch", action="store_true", help="Batch API (0.5x)")
    p.add_argument("--model"); p.add_argument("--verbose", action="store_true")
    p.set_defaults(f=cmd_api_usage)

    sub.add_parser("status").set_defaults(f=cmd_status)

    p = sub.add_parser("window")
    p.add_argument("--started-ago-min", type=int, help="window started N minutes ago")
    p.add_argument("--fresh", action="store_true", help="user was idle for the whole window: usage 0, window starts now")
    p.add_argument("--hours", type=float); p.set_defaults(f=cmd_window)

    p = sub.add_parser("calibrate"); p.add_argument("--budget", type=int)
    p.add_argument("--budget-usd", type=float)
    p.add_argument("--context-limit", type=int); p.add_argument("--cache-discount", type=float)
    p.add_argument("--model-weight", nargs="+", metavar="NAME=VALUE")
    p.add_argument("--price", nargs="+", metavar="NAME=IN/OUT", help="USD per MTok, e.g. opus-5.5=4/20")
    p.add_argument("--rpm", type=int); p.add_argument("--itpm", type=int); p.add_argument("--otpm", type=int)
    p.add_argument("--hit-limit", action="store_true")
    p.add_argument("--hit-rate-limit", action="store_true", help="after an HTTP 429")
    p.set_defaults(f=cmd_calibrate)

    p = sub.add_parser("export"); p.add_argument("--out"); p.set_defaults(f=cmd_export)
    p = sub.add_parser("restore"); p.add_argument("--file", required=True); p.set_defaults(f=cmd_restore)
    p = sub.add_parser("reset"); p.add_argument("--new-chat", action="store_true"); p.set_defaults(f=cmd_reset)

    a = ap.parse_args()
    if a.ledger:
        os.environ["YT_LEDGER"] = a.ledger
    a.f(a)


if __name__ == "__main__":
    main()
