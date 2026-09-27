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
from flask import Flask, request, jsonify

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
]

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


@app.route("/dashboard", methods=["GET"])
def dashboard():
    """
    A read-only, live status page — visit this URL in any browser to see
    real campaign data from this actual running server (auto-refreshes
    every 5 seconds). This is server-rendered, not a simulation.
    """
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
      <meta http-equiv="refresh" content="5">
      <title>Ledger DSP — Live Status</title>
      <style>
        body {{ background:#0E1420; color:#E7E4DA; font-family: ui-monospace, monospace; padding:24px; }}
        h1 {{ font-size:18px; }}
        .note {{ color:#8B94A6; font-size:12px; margin-bottom:20px; }}
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
      <div class="note">Auto-refreshes every 5s · real data from this running server · POST bid requests to /bid</div>
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
