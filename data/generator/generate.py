"""Synthetic chargeback corpus generator.

We cannot get real chargeback data, so we generate it - and say so loudly.
See docs/DATA-CARD.md for distribution, biases and limitations.

Two properties matter more than realism:

1. The ground-truth label ("should this representment have won?") is produced
   by an ORACLE that reads the case facts directly. It never consults the win
   model or the EV engine. Evaluating a model against labels the model helped
   write would be circular, and that circularity is the most common way a
   hackathon eval quietly lies.

2. Generation is deterministic from `--seed`, and the held-out split is hashed
   into data/test/MANIFEST.json. If the test set ever changes, the manifest
   changes, and the eval report is no longer comparable to earlier runs.

Usage:  python data/generator/generate.py --seed 20260824 --n 300
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRAIN_DIR = ROOT / "data" / "train"
TEST_DIR = ROOT / "data" / "test"

# Reason-code mix, roughly reflecting Indian card-not-present ecommerce:
# fraud claims dominate, non-delivery is close behind (COD/RTO culture),
# subjective quality disputes are a long tail.
REASON_MIX = [
    ("10.4", 0.34),
    ("13.1", 0.26),
    ("13.3", 0.14),
    ("13.2", 0.10),
    ("13.6", 0.09),
    ("12.6", 0.07),
]

REASON_TEXT = {
    "10.4": "Other Fraud - Card Absent Environment",
    "13.1": "Merchandise/Services Not Received",
    "13.3": "Not as Described or Defective Merchandise",
    "13.2": "Cancelled Recurring Transaction",
    "13.6": "Credit Not Processed",
    "12.6": "Duplicate Processing",
}

CARRIERS = ["Delhivery", "Bluedart", "Ekart", "XpressBees", "Shadowfax"]
CITIES = ["Bengaluru", "Mumbai", "Pune", "Hyderabad", "Chennai", "Jaipur", "Kolkata"]
STREETS = ["MG Road", "Park Street", "Hill View", "Lake Road", "Residency Lane"]
PRODUCTS = [
    ("SNK-4471", "Running Shoes", 499900),
    ("HDP-2210", "Wireless Headphones", 289900),
    ("WCH-9034", "Smart Watch", 1249900),
    ("BAG-5512", "Laptop Backpack", 189900),
    ("KIT-7781", "Cookware Set", 749900),
    ("SUB-0001", "Annual Subscription", 599900),
]

#: The corpus clock. Cases are generated relative to this instant, and
#: evals/run_eval.py reads the same one, so the split does not rot as the real
#: date moves past it.
CORPUS_NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)

#: Share of cases whose response window has already closed. These exist so the
#: expired-deadline path is exercised, not because real inboxes look like this.
EXPIRED_FRACTION = 0.08

#: Fraction of cases where the oracle's verdict is flipped, standing in for
#: issuer discretion. Real issuer decisions are not a pure function of the
#: evidence, and a model that scores 1.00 on noiseless data has learned nothing.
LABEL_NOISE = 0.08


def _weighted(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    r = rng.random()
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if r <= acc:
            return value
    return pairs[-1][0]


def _addr(rng: random.Random) -> str:
    house = rng.randint(1, 99)
    street = rng.choice(STREETS)
    city = rng.choice(CITIES)
    pin = rng.randint(110001, 799999)
    return f"{house} {street}, {city} {pin}"


def _support(rng: random.Random, idx: int, reason: str) -> dict:
    opener = {
        "10.4": "I never made this purchase.",
        "13.1": "My order still has not arrived.",
        "13.3": "The item is not what was shown on the site.",
        "13.2": "I cancelled this subscription last month.",
        "13.6": "You promised a refund and it never came.",
        "12.6": "I was charged twice for one order.",
    }[reason]
    return {
        "id": f"tkt_{idx:04d}",
        "messages": [
            {"at": "2026-08-10T09:12:00+00:00", "sender": "customer", "body": opener},
            {
                "at": "2026-08-10T11:40:00+00:00",
                "sender": "merchant",
                "body": "Thanks for reaching out - we are checking the order records.",
            },
        ],
    }


def make_case(rng: random.Random, idx: int) -> dict:
    reason = _weighted(rng, REASON_MIX)
    sku, title, price = rng.choice(PRODUCTS)
    qty = rng.choice([1, 1, 1, 2])
    amount = price * qty

    # Disputes are generated relative to CORPUS_NOW so the corpus looks like a
    # live inbox: most cases still inside their response window, a deliberate
    # minority already expired. Anchoring `placed` instead produced a test set
    # where 58% of deadlines had passed, which measured the calendar rather
    # than the model.
    disputed = CORPUS_NOW - timedelta(days=rng.randint(0, 8), hours=rng.randint(0, 23))
    placed = disputed - timedelta(days=rng.randint(6, 90))
    respond_by = disputed + timedelta(days=rng.choice([5, 7, 7, 10]))
    if rng.random() < EXPIRED_FRACTION:
        respond_by = CORPUS_NOW - timedelta(hours=rng.randint(1, 72))

    ship = _addr(rng)
    bill = ship if rng.random() < 0.82 else _addr(rng)
    device_id = f"dev_{rng.getrandbits(40):010x}"
    ip = f"49.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    account_id = f"acct_{rng.getrandbits(32):08x}"

    # ---- prior transaction history --------------------------------------
    priors = []
    n_priors = rng.choices([0, 1, 2, 3, 4], weights=[0.22, 0.18, 0.26, 0.20, 0.14])[0]
    for p in range(n_priors):
        age_days = rng.choice([40, 95, 130, 160, 210, 280, 340, 400])
        drift = rng.random()
        priors.append(
            {
                "payment_id": f"pay_prior{idx:04d}{p}",
                "paid_at": (disputed - timedelta(days=age_days)).isoformat(),
                "amount": rng.choice([pr[2] for pr in PRODUCTS]),
                "undisputed": rng.random() > 0.10,
                "device_id": device_id if drift < 0.72 else f"dev_{rng.getrandbits(40):010x}",
                "ip_address": ip if drift < 0.45 else f"49.{rng.randint(0, 255)}.1.1",
                "shipping_address": ship if drift < 0.80 else _addr(rng),
                "account_id": account_id if drift < 0.88 else f"acct_{rng.getrandbits(32):08x}",
            }
        )

    # ---- delivery proof --------------------------------------------------
    # Subscriptions have no physical delivery at all, so no POD can exist.
    physical = sku != "SUB-0001"
    delivery = None
    if physical and rng.random() < 0.74:
        signed = rng.random() < 0.55
        wrong_address = rng.random() < 0.09
        carrier = rng.choice(CARRIERS)
        delivery = {
            "tracking_id": f"{carrier[:3].upper()}{rng.getrandbits(30):09d}",
            "delivered_at": (placed + timedelta(days=rng.randint(2, 9))).isoformat(),
            "signed_by": rng.choice(["R. Kumar", "S. Iyer", "A. Sharma"]) if signed else None,
            "delivered_to_address": _addr(rng) if wrong_address else ship,
            "carrier": carrier,
            "document_uri": f"fixtures/pod/{idx:04d}.pdf",
            "source_spans": {"delivered_at": "p1 bbox[120,340,480,372]"},
        }

    has_support = rng.random() < 0.63
    has_policy = rng.random() < 0.86
    refunded = rng.random() < 0.11

    case = {
        "case_id": f"case_{idx:04d}",
        "dispute": {
            "id": f"disp_{rng.getrandbits(56):014x}",
            "payment_id": f"pay_{rng.getrandbits(56):014x}",
            "amount": amount,
            "currency": "INR",
            "reason_code": reason,
            "reason_description": REASON_TEXT[reason],
            "respond_by": respond_by.isoformat(),
            "status": "open",
            "phase": "fraud" if reason == "10.4" else "chargeback",
            "created_at": disputed.isoformat(),
        },
        "current_identifiers": {
            "payment_id": f"pay_{idx:04d}",
            "paid_at": placed.isoformat(),
            "amount": amount,
            "undisputed": False,
            "device_id": device_id,
            "ip_address": ip,
            "shipping_address": ship,
            "account_id": account_id,
        },
        "order": {
            "id": f"ord_{idx:04d}",
            "payment_id": f"pay_{idx:04d}",
            "placed_at": placed.isoformat(),
            "amount": amount,
            "items": [{"sku": sku, "title": title, "qty": qty, "unit_price": price}],
            "billing_address": bill,
            "shipping_address": ship,
            "customer_email": f"customer{idx:04d}@example.com",
            "customer_phone": f"+9198{rng.randint(10000000, 99999999)}",
        },
        "delivery": delivery,
        "support": _support(rng, idx, reason) if has_support else None,
        "device": {
            "device_id": device_id,
            "ip_address": ip,
            "user_agent": "Mozilla/5.0 (Linux; Android 14)",
            "seen_at": placed.isoformat(),
        },
        "prior_transactions": priors,
        "policy": (
            {
                "refund_policy_text": "Returns accepted within 7 days of delivery, unused.",
                "terms_text": "Standard merchant terms, accepted at checkout.",
                "effective_from": (placed - timedelta(days=200)).isoformat(),
                "document_uri": f"fixtures/policy/{idx:04d}.html",
            }
            if has_policy
            else None
        ),
        "refund_confirmation_uri": f"fixtures/refund/{idx:04d}.pdf" if refunded else None,
    }

    case["label"] = oracle_label(rng, case)
    return case


# ---------------------------------------------------------------------------
# ORACLE - ground truth. Reads case facts only. Never imports vakil.*
# ---------------------------------------------------------------------------


def _ce3_would_qualify(case: dict) -> bool:
    disputed = datetime.fromisoformat(case["dispute"]["created_at"])
    cur = case["current_identifiers"]
    eligible = []
    for t in case["prior_transactions"]:
        if not t["undisputed"]:
            continue
        age = (disputed - datetime.fromisoformat(t["paid_at"])).days
        if 120 <= age <= 365:
            eligible.append(t)
    if len(eligible) < 2:
        return False
    pair = sorted(eligible, key=lambda t: t["paid_at"])[:2]
    matched = sum(
        1
        for f in ("device_id", "ip_address", "shipping_address", "account_id")
        if cur.get(f) is not None and all(t.get(f) == cur[f] for t in pair)
    )
    return matched >= 2


def oracle_label(rng: random.Random, case: dict) -> dict:
    """Would an issuer have accepted a well-argued representment here?"""
    reason = case["dispute"]["reason_code"]
    d = case["delivery"]
    order = case["order"]
    delivered_to_right_address = bool(
        d and d.get("delivered_to_address") == order["shipping_address"]
    )
    signed = bool(d and d.get("signed_by"))

    if reason == "10.4":
        should_win = _ce3_would_qualify(case) or (signed and delivered_to_right_address)
        basis = "CE 3.0 prior relationship, or signed delivery to the order address"
    elif reason == "13.1":
        should_win = delivered_to_right_address and bool(d and d.get("delivered_at"))
        basis = "proof of delivery to the address on the order"
    elif reason == "13.3":
        should_win = bool(case["policy"]) and delivered_to_right_address and rng.random() < 0.35
        basis = "subjective quality claim, rarely rebuttable with documents alone"
    elif reason == "13.2":
        should_win = bool(case["policy"]) and bool(case["support"])
        basis = "cancellation terms plus a record of the cancellation request"
    elif reason == "13.6":
        should_win = case["refund_confirmation_uri"] is not None
        basis = "refund confirmation showing the credit was in fact processed"
    else:  # 12.6
        should_win = bool(order) and rng.random() < 0.72
        basis = "distinct order records for each charge"

    flipped = rng.random() < LABEL_NOISE
    noisy = (not should_win) if flipped else should_win

    return {
        "should_win": noisy,
        "oracle_verdict": should_win,
        "issuer_discretion_applied": flipped,
        "basis": basis,
    }


# ---------------------------------------------------------------------------


def write_split(cases: list[dict], directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for c in cases:
        (directory / f"{c['case_id']}.json").write_text(
            json.dumps(c, indent=2, sort_keys=True), encoding="utf-8"
        )


def manifest(cases: list[dict], seed: int) -> dict:
    blob = json.dumps(cases, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode()).hexdigest()
    by_reason: dict[str, int] = {}
    wins = 0
    for c in cases:
        code = c["dispute"]["reason_code"]
        by_reason[code] = by_reason.get(code, 0) + 1
        wins += c["label"]["should_win"]
    return {
        "seed": seed,
        "n": len(cases),
        "sha256": digest,
        "reason_code_distribution": dict(sorted(by_reason.items())),
        "positive_rate": round(wins / len(cases), 4),
        "label_noise": LABEL_NOISE,
        "generated_by": "data/generator/generate.py",
        "warning": "Synthetic data. No real cardholder or merchant records. See docs/DATA-CARD.md",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--test-size", type=int, default=100)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cases = [make_case(rng, i) for i in range(args.n)]
    rng.shuffle(cases)

    test, train = cases[: args.test_size], cases[args.test_size :]
    write_split(train, TRAIN_DIR)
    write_split(test, TEST_DIR)

    m = manifest(test, args.seed)
    (TEST_DIR / "MANIFEST.json").write_text(json.dumps(m, indent=2), encoding="utf-8")

    print(f"train {len(train)}  test {len(test)}")
    print(f"test sha256      {m['sha256']}")
    print(f"positive rate    {m['positive_rate']}")
    print(f"reason mix       {m['reason_code_distribution']}")


if __name__ == "__main__":
    main()
