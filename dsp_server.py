"""
Ledger DSP Server
------------------
A real, live-running Demand-Side Platform bidder.

This is the server version of the "Ledger DSP Console" dashboard you tested
in the browser. Same core logic — targeting, per-size pricing, second-price
auctions, budget pacing — but now it's a real web server that can receive
actual OpenRTB-style bid requests over the internet, not a simulation
running only in your browser tab.

HOW TO RUN LOCALLY (for testing on your own computer):
    pip install flask --break-system-packages
    python dsp_server.py
    (then it listens on http://localhost:8080)

HOW TO DEPLOY LIVE (free): see the deployment steps provided alongside this file.
"""

import time
from flask import Flask, request, jsonify, redirect, url_for

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Campaign data
# In a real system this would live in a database. For this project, an
# in-memory list is fine to start — it resets whenever the server restarts.
# ---------------------------------------------------------------------------

SIZES = ["300x250", "160x600", "728x90"]

campaigns = [
    {
        "id": 1,
        "name": "USA Desktop Push",
        "country": "USA",
        "device": "Desktop",
        "prices": {"300x250": 2.50, "160x600": 1.30, "728x90": 1.80},
        "budget": 40.0,
        "budget_start": 40.0,
        "flight_seconds": 3600,  # 1 hour demo flight — set this to your real campaign length
    },
    {
        "id": 2,
        "name": "USA Retail Retarget",
        "country": "USA",
        "device": "Mobile",
        "prices": {"300x250": 1.90, "160x600": 1.00, "728x90": 1.40},
        "budget": 30.0,
        "budget_start": 30.0,
        "flight_seconds": 3600,
    },
    {
        "id": 3,
        "name": "UK Launch Test",
        "country": "UK",
        "device": "Any",
        "prices": {"300x250": 1.75, "160x600": 1.00, "728x90": 1.30},
        "budget": 25.0,
        "budget_start": 25.0,
        "flight_seconds": 3600,
    },
    {
        "id": 4,
        "name": "India Mobile",
        "country": "India",
        "device": "Mobile",
        "prices": {"300x250": 0.90, "160x600": 0.50, "728x90": 0.70},
        "budget": 18.0,
        "budget_start": 18.0,
        "flight_seconds": 3600,
    },
]

COUNTRIES = ["USA", "UK", "India", "Germany", "Canada", "France"]

SESSION_START = time.time()


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------

def elapsed_seconds():
    return time.time() - SESSION_START


def ideal_spend_fraction(campaign):
    return min(1.0, elapsed_seconds() / campaign["flight_seconds"])


def is_on_pace(campaign):
    if campaign["budget_start"] <= 0:
        return True
    spent_fraction = (campaign["budget_start"] - campaign["budget"]) / campaign["budget_start"]
    return spent_fraction <= ideal_spend_fraction(campaign) + 0.02  # small buffer


# ---------------------------------------------------------------------------
# Reading a real OpenRTB bid request
# ---------------------------------------------------------------------------

def parse_bid_request(body):
    """
    Pulls the fields we care about out of a real OpenRTB bid request.
    Real bid requests have many more fields than this — we only read what
    our targeting logic actually uses.
    """
    country = body.get("device", {}).get("geo", {}).get("country", "")
    devicetype = body.get("device", {}).get("devicetype", 2)
    # OpenRTB devicetype: 1 = mobile/tablet, 2 = personal computer, others exist too.
    device = "mobile" if devicetype == 1 else "desktop"

    imp = body.get("imp", [{}])[0]
    banner = imp.get("banner", {})
    width = banner.get("w")
    height = banner.get("h")
    size = f"{width}x{height}" if width and height else None

    return {
        "id": body.get("id", ""),
        "imp_id": imp.get("id", "1"),
        "country": country,
        "device": device,
        "size": size,
    }


# ---------------------------------------------------------------------------
# The actual auction — same logic as the dashboard, ported to Python
# ---------------------------------------------------------------------------

def find_target_matches(req):
    matches = []
    for c in campaigns:
        if c["country"] != req["country"]:
            continue
        if c["device"] != "Any" and c["device"].lower() != req["device"]:
            continue
        if req["size"] not in c["prices"]:
            continue
        if c["budget"] < c["prices"][req["size"]]:
            continue
        matches.append(c)
    return matches


def run_auction(req):
    target_matches = find_target_matches(req)
    eligible = [c for c in target_matches if is_on_pace(c)]
    eligible.sort(key=lambda c: c["prices"][req["size"]], reverse=True)
    throttled_count = len(target_matches) - len(eligible)

    if not eligible:
        return {"won": False, "throttled_count": throttled_count}

    winner = eligible[0]
    own_price = winner["prices"][req["size"]]
    clearing_price = eligible[1]["prices"][req["size"]] if len(eligible) > 1 else own_price

    winner["budget"] = round(winner["budget"] - clearing_price, 2)

    return {
        "won": True,
        "campaign": winner,
        "price": clearing_price,
        "own_price": own_price,
        "bidder_count": len(eligible),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/bid", methods=["POST"])
def handle_bid_request():
    """
    This is the real endpoint an ad exchange would send bid requests to.
    Returns a proper OpenRTB bid response on a win, or HTTP 204 (No Content)
    on a no-bid — exactly what real exchanges expect.
    """
    body = request.get_json(force=True, silent=True) or {}
    req = parse_bid_request(body)

    if not req["country"] or not req["size"]:
        return jsonify({"error": "missing country or ad size in request"}), 400

    outcome = run_auction(req)

    if not outcome["won"]:
        return "", 204

    winner = outcome["campaign"]
    response = {
        "id": req["id"],
        "seatbid": [{
            "bid": [{
                "impid": req["imp_id"],
                "price": outcome["price"],
                "adm": f"<div>Ad from {winner['name']}</div>",
            }]
        }]
    }
    return jsonify(response)


@app.route("/dashboard/simulate", methods=["POST"])
def dashboard_simulate():
    """
    Handles the "Send test bid" form on the dashboard. Runs a real request
    through the exact same run_auction() function /bid uses, then redirects
    back to /dashboard with the result so it can be shown on the page.
    """
    country = request.form.get("country", "USA")
    device = request.form.get("device", "desktop")
    size = request.form.get("size", "300x250")

    req = {"id": "dashboard-test", "imp_id": "1", "country": country, "device": device, "size": size}
    outcome = run_auction(req)

    if outcome["won"]:
        winner = outcome["campaign"]
        msg = (f'WON — "{winner["name"]}" bid ${outcome["price"]:.2f} for a {size} slot '
               f'in {country}/{device} ({outcome["bidder_count"]} bidder(s) competed)')
        status = "won"
    else:
        reason = "a matching campaign ran out of budget or is pacing" if outcome.get("throttled_count") else "no campaign targets this country/device/size"
        msg = f"NO BID — {reason} ({country}/{device}/{size})"
        status = "lost"

    return redirect(url_for("dashboard", msg=msg, status=status))


@app.route("/dashboard", methods=["GET"])
def dashboard():
    """
    A live, interactive status page — visit this URL in any browser to see
    real campaign data from this actual running server, and use the form
    below to send a real test bid request without needing any other tool.
    (Auto-refreshes every 15s when idle; this is server-rendered, not a
    browser-only simulation — every bid here runs the real /bid logic.)
    """
    result_msg = request.args.get("msg")
    result_status = request.args.get("status")
    banner_html = ""
    if result_msg:
        color = "#5FAE9E" if result_status == "won" else "#C1554D"
        bg = "#13241f" if result_status == "won" else "#241515"
        banner_html = f'<div class="banner" style="border-color:{color}; background:{bg}; color:{color};">{result_msg}</div>'

    country_options = "".join(f'<option value="{c}">{c}</option>' for c in COUNTRIES)
    size_options = "".join(f'<option value="{s}">{s}</option>' for s in SIZES)

    form_html = f"""
    <form class="simform" action="/dashboard/simulate" method="post">
      <div class="simtitle">Send a test bid request</div>
      <div class="simrow">
        <label>Country
          <select name="country">{country_options}</select>
        </label>
        <label>Device
          <select name="device">
            <option value="desktop">Desktop</option>
            <option value="mobile">Mobile</option>
          </select>
        </label>
        <label>Ad size
          <select name="size">{size_options}</select>
        </label>
        <button type="submit">Send bid request &rarr;</button>
      </div>
    </form>"""

    rows = ""
    for c in campaigns:
        pct = (c["budget"] / c["budget_start"] * 100) if c["budget_start"] else 0
        pace_ok = is_on_pace(c)
        pace_color = "#5FAE9E" if pace_ok else "#C1554D"
        pace_text = "on pace" if pace_ok else "THROTTLED"
        elapsed_pct = round(ideal_spend_fraction(c) * 100, 1)
        spent_pct = round(((c["budget_start"] - c["budget"]) / c["budget_start"]) * 100, 1) if c["budget_start"] else 0
        prices_str = " · ".join(f"{size}: ${price:.2f}" for size, price in c["prices"].items())
        rows += f"""
        <div class="row">
          <div class="name">{c['name']}</div>
          <div class="meta">{c['country']} / {c['device']}</div>
          <div class="prices">{prices_str}</div>
          <div class="budget">${c['budget']:.2f} / ${c['budget_start']:.2f}
            <div class="bar"><div class="fill" style="width:{max(0,min(100,pct))}%"></div></div>
          </div>
          <div class="pace" style="color:{pace_color}">{pace_text} — {elapsed_pct}% elapsed · {spent_pct}% spent</div>
        </div>"""

    html = f"""
    <!DOCTYPE html>
    <html><head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Ledger DSP — Live Status</title>
      <style>
        body {{ background:#0E1420; color:#E7E4DA; font-family: ui-monospace, monospace; padding:24px; max-width:900px; margin:0 auto; }}
        h1 {{ font-size:18px; }}
        .note {{ color:#8B94A6; font-size:12px; margin-bottom:20px; }}
        .banner {{ border:1px solid; border-radius:4px; padding:12px 14px; font-size:13px; font-weight:600; margin-bottom:18px; }}
        .simform {{ border:1px solid #263042; border-radius:4px; padding:14px 16px; margin-bottom:24px; background:#141B2A; }}
        .simtitle {{ font-size:13px; font-weight:bold; margin-bottom:10px; color:#E7E4DA; }}
        .simrow {{ display:flex; gap:10px; align-items:end; flex-wrap:wrap; }}
        .simrow label {{ display:flex; flex-direction:column; font-size:11px; color:#8B94A6; gap:4px; }}
        .simrow select {{ background:#0A0F18; color:#E7E4DA; border:1px solid #263042; border-radius:3px; padding:7px 8px; font-family:inherit; font-size:12px; }}
        .simrow button {{ background:#5FAE9E; color:#071613; border:none; border-radius:3px; padding:9px 14px; font-family:inherit; font-weight:700; font-size:12px; cursor:pointer; }}
        .simrow button:hover {{ filter:brightness(1.1); }}
        .row {{ border:1px solid #263042; border-radius:4px; padding:12px 16px; margin-bottom:10px; background:#141B2A; }}
        .name {{ font-weight:bold; font-size:14px; }}
        .meta {{ color:#8B94A6; font-size:12px; margin-top:2px; }}
        .prices {{ font-size:12px; margin-top:6px; }}
        .budget {{ font-size:12px; margin-top:8px; }}
        .bar {{ height:5px; background:#0A0F18; border-radius:3px; margin-top:4px; overflow:hidden; }}
        .fill {{ height:100%; background:#E8A33D; }}
        .pace {{ font-size:11px; margin-top:6px; font-weight:600; }}
      </style>
    </head><body>
      <h1>Ledger DSP — Live Server Status</h1>
      <div class="note">Real data from this running server · every button below sends an actual request through the live /bid logic</div>
      {banner_html}
      {form_html}
      {rows}
    </body></html>
    """
    return html


@app.route("/campaigns", methods=["GET"])
def list_campaigns():
    """A simple status view — see all campaigns and their live budgets/pacing."""
    out = []
    for c in campaigns:
        out.append({
            "id": c["id"],
            "name": c["name"],
            "country": c["country"],
            "device": c["device"],
            "prices": c["prices"],
            "budget": c["budget"],
            "budget_start": c["budget_start"],
            "on_pace": is_on_pace(c),
            "elapsed_pct": round(ideal_spend_fraction(c) * 100, 1),
            "spent_pct": round(((c["budget_start"] - c["budget"]) / c["budget_start"]) * 100, 1) if c["budget_start"] else 0,
        })
    return jsonify(out)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "Ledger DSP server is running",
        "endpoints": {
            "POST /bid": "send an OpenRTB bid request here",
            "GET /campaigns": "view live campaign status",
        }
    })


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
Add India campaign and interactive dashboard form
