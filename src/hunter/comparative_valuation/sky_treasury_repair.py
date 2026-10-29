"""Receipt-backed reconciliation of duplicate discovery identities."""
import json
from pathlib import Path

from hunter.comparative_valuation.ethereum_rpc import TRANSFER_TOPIC
from hunter.comparative_valuation.sky_treasury_fast_audit import SKY_TOKEN, rpc_batch_receipts, save


def repair(path: Path, max_batches: int = 20) -> dict:
    state = json.loads(path.read_text())
    if state.get("schema") != "sky-fast-treasury/v1":
        raise ValueError("wrong checkpoint")
    skipped = state.setdefault("phantom_discovery", {})
    pending = sorted(set(state["candidates"]) - set(state["verified"]) - set(skipped))
    for pos in range(0, min(len(pending), max_batches * 10), 10):
        keys = pending[pos:pos + 10]
        receipts = rpc_batch_receipts(sorted({state["candidates"][k]["tx"] for k in keys}))
        for key in keys:
            row = state["candidates"][key]
            receipt = receipts[row["tx"]]
            if int(receipt["blockNumber"], 16) != row["block"] or receipt.get("status") != "0x1":
                raise ValueError("receipt mismatch")
            matches = []
            for log in receipt["logs"]:
                if log["address"].lower() != SKY_TOKEN or len(log["topics"]) != 3 or log["topics"][0].lower() != TRANSFER_TOPIC:
                    continue
                source = "0x" + log["topics"][1][-40:].lower()
                dest = "0x" + log["topics"][2][-40:].lower()
                if (source, dest, int(log["data"], 16)) == (row["from"], row["to"], int(row["raw"])):
                    matches.append(log)
            if not matches:
                raise ValueError("discovery not found in receipt")
            matching_index = [log for log in matches if int(log["logIndex"], 16) == row["log_index"]]
            if matching_index:
                state["verified"][key] = {**row, "block_hash": receipt["blockHash"].lower()}
            elif len(matches) == 1:
                canon = row["tx"] + ":" + str(int(matches[0]["logIndex"], 16))
                peer = state["candidates"].get(canon)
                if not peer or any(peer[f] != row[f] for f in ("tx", "block", "from", "to", "raw")):
                    raise ValueError("no equivalent canonical log")
                skipped[key] = {"reason": "duplicate_virtual_index", "canonical": canon}
            else:
                raise ValueError("ambiguous log match")
        save(path, state)
    return {"verified": len(state["verified"]), "duplicates": len(skipped),
            "remaining": len(set(state["candidates"]) - set(state["verified"]) - set(skipped))}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--max-batches", type=int, default=20)
    args = ap.parse_args()
    print(json.dumps(repair(args.checkpoint, args.max_batches), sort_keys=True))
