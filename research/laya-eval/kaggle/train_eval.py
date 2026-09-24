"""Kaggle kernel: fine-tune Laya typed-decisions on our labeled states, evaluate per EVAL_PROTOCOL.md.

Reads everything from the private dataset wuaidan/laya-eval-data (found by searching /kaggle/input
for config.json): config.json, questions.py, synth_train/val/test.jsonl, real_test.jsonl.

Phases (metrics.json is rewritten after each, so a timeout still leaves partial results):
  1. zero-shot eval (laya.Agent, shipped temperatures) on real_test + synth_test
  2. fine-tune on synth_train with the official notebook's RLCD objective (noisy-logit policy
     gradient on a proper scoring rule + soft cross-entropy), single process, both T4s via
     nn.DataParallel. Val pass + temperature fit + checkpoint every epoch; early stopping on val loss;
     stops early to leave room for phase 3 inside time_budget_min.
  3. trained eval (laya.Agent on the best checkpoint) on the same sets + 3 robustness variants.

Model input transform (applied identically at train and eval): state_mode "trim" = questions.trim
(drop persona and trends, keep 4 newest ticks and 8 last today lines), then the robustness
alteration if any. Labels are always the ones written for the full state.
"""
import gc, glob, json, math, os, random, shutil, subprocess, sys, time

T_START = time.time()
WORK = "/kaggle/working"
LAYA_COMMIT = "23a1752"


def log(*a):
    print("[%6.0fs]" % (time.time() - T_START), *a, flush=True)


def elapsed():
    return time.time() - T_START


# ---------------------------------------------------------------- setup
t = time.time()
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps",
                "git+https://github.com/NandhaKishorM/laya@%s" % LAYA_COMMIT], check=True)
INSTALL_S = time.time() - t

import numpy as np  # noqa: E402
import torch  # noqa: E402
import transformers  # noqa: E402
import laya  # noqa: E402
from laya.agent import Agent  # noqa: E402
from laya.common import QTYPES, build_sequence, collate_items, ece_score, proper_reward, render_options  # noqa: E402
from safetensors.torch import save_file  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

cfg_paths = [p for p in glob.glob("/kaggle/input/**/config.json", recursive=True)
             if os.path.exists(os.path.join(os.path.dirname(p), "questions.py"))]
assert cfg_paths, "config.json + questions.py not found under /kaggle/input: %s" % os.listdir("/kaggle/input")
DATA = os.path.dirname(cfg_paths[0])
sys.path.insert(0, DATA)
from questions import TOPICS, URGENCY, laya_questions, trim  # noqa: E402

C = {"mode": "smoke", "max_train": 0, "epochs": 1, "max_len": 1024, "lr": 2.5e-5, "time_budget_min": 25,
     "state_mode": "trim", "keep_ckpt": False, "head_max_len": 256, "micro_batch": 16, "grad_accum": 4,
     "eval_batch_states": 8, "patience": 1, "margin_s": 180, "data_parallel": True, "label_smoothing": 0.0,
     "grad_ckpt": True, "seed": 0}
C.update(json.load(open(cfg_paths[0])))
BUDGET_S = C["time_budget_min"] * 60
log("data dir", DATA, os.listdir(DATA))
log("config", C)

Q = laya_questions()
QIDS = list(Q)
NOUL = [q for q in QIDS if Q[q]["type"] == "noul"]
INTERNAL = {q: Agent._to_internal(Q[q]) for q in QIDS}


def read(name):
    p = os.path.join(DATA, name + ".jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else None


SETS = {k: read(k) for k in ("synth_train", "synth_val", "synth_test", "real_test")}
if C["max_train"] and SETS["synth_train"]:
    random.Random(C["seed"]).shuffle(SETS["synth_train"])
    SETS["synth_train"] = SETS["synth_train"][: C["max_train"]]
EVAL_SETS = [k for k in ("real_test", "synth_test") if SETS[k]]
log("sizes", {k: (len(v) if v else 0) for k, v in SETS.items()})


def model_input(state, variant=None):
    s = trim(state) if C["state_mode"] == "trim" else dict(state)
    if variant == "extra_field":
        # after trigger, so right-truncation at max_len cannot silently drop it
        items = list(s.items())
        s = dict(items[:1] + [("weather", {"temp_c": 31, "sky": "clear"})] + items[1:])
    elif variant == "shuffled_keys":
        ks = list(s)
        random.Random(hash_id(state)).shuffle(ks)
        s = {k: s[k] for k in ks}
    elif variant == "renamed_key":
        s = {("today_log" if k == "today" else k): v for k, v in s.items()}
    return s


def hash_id(state):
    return sum(ord(c) for c in json.dumps(state["clock"])) + len(json.dumps(state))


def gold_index(lab, q):
    if Q[q]["type"] == "noul":
        return int(bool(lab[q]))
    if q == "topic":
        return TOPICS.index(lab[q]) if lab[q] in TOPICS else TOPICS.index("other")
    return URGENCY.index(lab[q])


# ---------------------------------------------------------------- metrics
def probs_from_answers(ans):
    """Agent answers -> {qid: option-probability list in option order}."""
    out = {}
    for q in QIDS:
        a = ans[q]
        if a["type"] == "noul":
            out[q] = [1 - a["noul"], a["noul"]]
        elif a["type"] == "choice":
            out[q] = [a["probabilities"][k] for k in TOPICS]
        else:
            out[q] = [a["probabilities"][str(i)] for i in range(len(URGENCY))]
    return out


def set_metrics(probs, rows):
    """probs: list of {qid: option probs}; rows: labeled examples, same order."""
    m = {"n": len(rows), "questions": {}}
    f1s, confs, corrects = [], [], []
    for q in NOUL:
        p = np.array([pr[q][1] for pr in probs], dtype=float)
        y = np.array([int(bool(r["labels"][q])) for r in rows])
        yh = (p >= 0.5).astype(int)
        tp, fp, fn = int(((yh == 1) & (y == 1)).sum()), int(((yh == 1) & (y == 0)).sum()), int(((yh == 0) & (y == 1)).sum())
        f1 = None if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)
        auc = float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None
        m["questions"][q] = {"acc": float((yh == y).mean()), "f1_yes": f1, "auroc": auc,
                             "pred_yes_rate": float(yh.mean()), "true_yes_rate": float(y.mean()),
                             "n_pos": int(y.sum())}
        if f1 is not None:
            f1s.append(f1)
        confs += list(np.maximum(p, 1 - p)); corrects += list((yh == y).astype(float))
    m["macro_f1"] = float(np.mean(f1s)) if f1s else None
    m["macro_f1_n_questions"] = len(f1s)
    m["speak_f1"] = m["questions"]["speak"]["f1_yes"]
    m["ask_f1"] = m["questions"]["ask"]["f1_yes"]
    m["ece"] = ece_score(np.array(confs), np.array(corrects), bins=10)
    tp_ = np.array([int(np.argmax(pr["topic"])) for pr in probs])
    tg = np.array([gold_index(r["labels"], "topic") for r in rows])
    m["topic_acc"] = float((tp_ == tg).mean())
    up = np.array([pr["urgency"] for pr in probs], dtype=float)
    ug = np.array([gold_index(r["labels"], "urgency") for r in rows])
    m["urgency_acc"] = float((up.argmax(1) == ug).mean())
    m["urgency_acc_rounded_expected"] = float((np.rint(up @ np.arange(up.shape[1])) == ug).mean())
    return m


def eval_agent(agent, rows, variant=None, tag=""):
    t = time.time()
    states = [model_input(r["state"], variant) for r in rows]
    res = agent.predict_batch(states, Q, batch_size=C["eval_batch_states"], max_len=C["max_len"],
                              head_max_len=C["head_max_len"])
    probs = [probs_from_answers(r["answers"]) for r in res]
    os.makedirs(os.path.join(WORK, "preds"), exist_ok=True)
    with open(os.path.join(WORK, "preds", tag + ".jsonl"), "w") as f:
        for r, p in zip(rows, probs):
            f.write(json.dumps({"id": r["id"], "labels": r["labels"],
                                "p": {q: [round(float(v), 4) for v in p[q]] for q in QIDS}}) + "\n")
    m = set_metrics(probs, rows)
    m["eval_s"] = round(time.time() - t, 1)
    log("eval", tag, "macroF1=%s speakF1=%s askF1=%s topic=%.3f urg=%.3f ece=%.3f (%.0fs)" % (
        m["macro_f1"], m["speak_f1"], m["ask_f1"], m["topic_acc"], m["urgency_acc"], m["ece"], m["eval_s"]))
    return m


M = {"status": "running", "config": C, "method": "official RLCD objective (noisy-logit policy gradient on "
     "proper scoring reward + soft CE), single-process, DataParallel over available GPUs",
     "state_transform": "questions.trim (no persona/trends, 4 ticks, 8 today lines)" if C["state_mode"] == "trim" else "full state",
     "env": {"gpus": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
             "torch": torch.__version__, "transformers": transformers.__version__, "laya_commit": LAYA_COMMIT},
     "sizes": {k: (len(v) if v else 0) for k, v in SETS.items()},
     "models": {"zero_shot": {}, "trained": {}}, "train": {}, "wall": {"install_s": round(INSTALL_S, 1)}}


def save_metrics():
    M["wall"]["total_s"] = round(elapsed(), 1)
    json.dump(M, open(os.path.join(WORK, "metrics.json"), "w"), indent=1)
    write_md()


def fmt(x):
    return "-" if x is None else ("%.3f" % x if isinstance(x, float) else str(x))


def write_md():
    L = ["# Laya eval metrics (%s run, status %s)" % (C["mode"], M["status"]), "",
         "| model | set | n | macro-F1 (8 y/n) | speak F1 | ask F1 | topic acc | urgency acc | ECE |",
         "|---|---|---|---|---|---|---|---|---|"]
    for mod, sets in M["models"].items():
        for s, m in sets.items():
            L.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s |" % (
                mod, s, m["n"], fmt(m["macro_f1"]), fmt(m["speak_f1"]), fmt(m["ask_f1"]),
                fmt(m["topic_acc"]), fmt(m["urgency_acc"]), fmt(m["ece"])))
    if M.get("robustness"):
        L += ["", "Robustness (trained, real_test): macro-F1 drop vs unaltered = " +
              ", ".join("%s %s" % (k, fmt(v["drop"])) for k, v in M["robustness"].items() if k != "base_macro_f1")]
    tr = M["train"]
    if tr:
        L += ["", "Train: %s epochs done, best epoch %s, %s states/s (%s question-rows/s), stop: %s" % (
            fmt(tr.get("epochs_done")), tr.get("best_epoch"), fmt(tr.get("states_per_s")),
            fmt(tr.get("rows_per_s")), tr.get("stop_reason"))]
    L += ["", "Wall (s): " + ", ".join("%s %s" % (k, v) for k, v in M["wall"].items()),
          "", "F1(yes) is undefined (excluded from macro-F1) when a question has no true and no predicted yes."]
    open(os.path.join(WORK, "metrics.md"), "w").write("\n".join(L) + "\n")


# ---------------------------------------------------------------- phase 1: zero-shot
t = time.time()
agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device="cuda")
M["wall"]["load_s"] = round(time.time() - t, 1)
log("loaded; dtype", agent.dtype, "cfg max_len", agent.cfg.get("max_len"))
t = time.time()
for s in EVAL_SETS:
    M["models"]["zero_shot"][s] = eval_agent(agent, SETS[s], tag="zero_shot__" + s)
    save_metrics()
ZS_EVAL_S = time.time() - t
M["wall"]["zero_shot_eval_s"] = round(ZS_EVAL_S, 1)
n_zs = sum(len(SETS[s]) for s in EVAL_SETS)
eval_state_s = ZS_EVAL_S / max(1, n_zs)
n_final = sum(len(SETS[s]) for s in EVAL_SETS) + 3 * (len(SETS["real_test"]) if SETS["real_test"] else 0)
FINAL_RESERVE = eval_state_s * n_final * 1.3 + 60 + (40 if C["keep_ckpt"] else 0)
log("zero-shot eval %.0fs (%.2fs/state); final-phase reserve %.0fs" % (ZS_EVAL_S, eval_state_s, FINAL_RESERVE))


# ---------------------------------------------------------------- phase 2: train
def build_items(rows):
    tok = agent.tok
    items = []
    ls = C["label_smoothing"]
    for r in rows:
        st = model_input(r["state"])
        sid = tok(json.dumps(st, ensure_ascii=False).replace(tok.mask_token, " "), add_special_tokens=False)["input_ids"]
        for q in QIDS:
            iq = INTERNAL[q]
            seq, mk = build_sequence(tok, st, iq, C["max_len"], C["head_max_len"], state_ids=sid)
            k = len(render_options(iq))
            assert len(mk) == k, (q, len(mk), k)
            g = gold_index(r["labels"], q)
            tgt = [(1 - ls) * (i == g) + ls / k for i in range(k)]
            items.append({"ids": seq, "markers": mk, "qtype": QTYPES[iq["t"]], "target": tgt, "label": g, "q": q})
    return items


def fit_one_temp(sel):
    """Official notebook's per-type temperature fit (LBFGS on log T), clamped to laya's [0.5, 5]."""
    if len(sel) < 10:
        return 1.0
    kmax = max(len(z) for z, _ in sel)
    Z = torch.full((len(sel), kmax), -1e4)
    T = torch.zeros((len(sel), kmax))
    for i, (z, tt) in enumerate(sel):
        Z[i, :len(z)] = torch.tensor(z); T[i, :len(tt)] = torch.tensor(tt, dtype=torch.float32)
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():
        opt.zero_grad()
        loss = -(T * torch.log_softmax(Z / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss
    opt.step(closure)
    return float(torch.clamp(log_t.exp(), 0.5, 5.0).item())


def val_pass(model, items, rows, device):
    model.eval()
    logits = []
    with torch.no_grad():
        for i in range(0, len(items), 32):
            b = collate_items([items[i:i + 32]], agent.tok.pad_token_id)
            with torch.autocast("cuda", dtype=torch.float16):
                z, _ = model(b["input_ids"].to(device), b["attention_mask"].to(device), b["marker_pos"].to(device),
                             b["marker_mask"].to(device), b["qtype"].to(device))
            z = z.float().cpu().numpy()
            for j, it in enumerate(items[i:i + 32]):
                logits.append(z[j, :len(it["markers"])])
    model.train()
    ce = float(np.mean([-np.sum(np.array(it["target"]) * (z - z.max() - np.log(np.exp(z - z.max()).sum())))
                        for z, it in zip(logits, items)]))
    temps = [1.0, 1.0, 1.0]
    for qt in range(3):
        sel = [(z, it["target"]) for z, it in zip(logits, items) if it["qtype"] == qt]
        if sel:
            temps[qt] = fit_one_temp(sel)
    probs, n = [], len(QIDS)
    for s in range(len(rows)):
        d = {}
        for j, q in enumerate(QIDS):
            z = logits[s * n + j] / temps[items[s * n + j]["qtype"]]
            e = np.exp(z - z.max()); d[q] = list(e / e.sum())
        probs.append(d)
    return ce, temps, set_metrics(probs, rows)


def save_ckpt(model, path, temps, meta):
    os.makedirs(path, exist_ok=True)
    save_file({k: v.half().contiguous().cpu() for k, v in model.state_dict().items()},
              os.path.join(path, "model.safetensors"))
    model.encoder.config.save_pretrained(os.path.join(path, "encoder"))
    agent.tok.save_pretrained(os.path.join(path, "tokenizer"))
    cfg = dict(agent.cfg)
    cfg.update({"fine_tuned": True, "temperature": temps, "max_len": C["max_len"], "head_max_len": C["head_max_len"],
                "finetune_meta": meta})
    cfg.pop("temperature_by_options", None)  # the fit is per type; inherited buckets would hide it
    json.dump(cfg, open(os.path.join(path, "rl_agent_config.json"), "w"), indent=1)


train_rows, val_rows = SETS["synth_train"] or [], SETS["synth_val"] or []
best = None
tr = M["train"]
if train_rows:
    device = torch.device("cuda")
    t = time.time()
    train_items, val_items = build_items(train_rows), build_items(val_rows)
    log("items: train %d, val %d (%.0fs); train seq len median %d" % (
        len(train_items), len(val_items), time.time() - t, int(np.median([len(i["ids"]) for i in train_items]))))
    model = agent.model
    model.train()
    if C["grad_ckpt"]:
        model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.head_checkpointing = True
    fwd = model
    if C["data_parallel"] and torch.cuda.device_count() > 1:
        fwd = torch.nn.DataParallel(model)
    enc = [p for n_, p in model.named_parameters() if n_.startswith("encoder.")]
    head = [p for n_, p in model.named_parameters() if not n_.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc, "lr": C["lr"]}, {"params": head, "lr": 4 * C["lr"]}], weight_decay=0.01)
    MB, GA, G = C["micro_batch"], C["grad_accum"], 4
    total_updates = max(1, math.ceil(len(train_items) / (MB * GA)) * C["epochs"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=total_updates, eta_min=1e-6)
    scaler = torch.amp.GradScaler("cuda")
    tr.update({"history": [], "rows_seen": 0, "step_s": 0.0, "stop_reason": "epochs completed",
               "micro_batch": MB, "grad_accum": GA, "data_parallel": isinstance(fwd, torch.nn.DataParallel)})
    val_s_est = eval_state_s * len(val_rows) * 1.5 + 30
    bad, stop = 0, False
    ep_done = 0.0
    for ep in range(C["epochs"]):
        rng = random.Random(C["seed"] + ep)
        order = list(range(len(train_items))); rng.shuffle(order)
        sigma = 0.4 + (0.1 - 0.4) * (ep / max(1, C["epochs"] - 1))
        opt.zero_grad(set_to_none=True)
        losses, n_mb, nb = [], math.ceil(len(order) / MB), 0
        for bi in range(n_mb):
            if elapsed() + FINAL_RESERVE + val_s_est + C["margin_s"] > BUDGET_S:
                stop, tr["stop_reason"] = True, "time budget"
                break
            ts = time.time()
            chunk = [train_items[i] for i in order[bi * MB:(bi + 1) * MB]]
            b = collate_items([chunk], agent.tok.pad_token_id)
            with torch.autocast("cuda", dtype=torch.float16):
                logits, act = fwd(b["input_ids"].to(device), b["attention_mask"].to(device), b["marker_pos"].to(device),
                                  b["marker_mask"].to(device), b["qtype"].to(device))
            logits = logits.float()
            mask = b["marker_mask"].to(device)
            k = mask.sum(-1, keepdim=True).float()
            target = b["target"].to(device)
            eps = torch.randn((G,) + logits.shape, device=device) * sigma * mask
            eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
            z = logits.detach().unsqueeze(0) + eps
            qd = torch.softmax(z.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                r = proper_reward(qd, target.unsqueeze(0), b["qtype"].to(device), mask, w_sph=0.75, w_rps=1.0)
                adv = r - r.mean(0, keepdim=True)
                adv = adv / (adv.std() + 1e-6)
            logp = -(((z - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            loss_rl = -(adv * logp).mean()
            loss_ce = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean()
            loss = (loss_rl + loss_ce) / GA + 0.0 * act.sum()
            scaler.scale(loss).backward()
            if (bi + 1) % GA == 0 or bi == n_mb - 1:
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(opt); scaler.update(); sched.step()
                opt.zero_grad(set_to_none=True)
            losses.append(float(loss_ce.item()))
            tr["step_s"] += time.time() - ts
            tr["rows_seen"] += len(chunk)
            nb += 1
            if nb % 25 == 0:
                log("ep %d mb %d/%d ce %.4f  %.1f rows/s" % (ep + 1, nb, n_mb, np.mean(losses[-25:]),
                                                           tr["rows_seen"] / tr["step_s"]))
        if nb == 0:
            break
        ep_done += nb / n_mb
        tv = time.time()
        vce, temps, vm = val_pass(model, val_items, val_rows, device) if val_items else (float(np.mean(losses)), [1.0] * 3, None)
        val_s_est = time.time() - tv + 30
        name = "ckpt_ep%d" % (ep + 1)
        save_ckpt(model, os.path.join(WORK, name), temps, {"epoch": ep + 1, "fraction": nb / n_mb, "val_ce": vce})
        h = {"epoch": ep + 1, "fraction_of_epoch": round(nb / n_mb, 3), "train_ce": float(np.mean(losses)),
             "val_ce": vce, "val_macro_f1": vm["macro_f1"] if vm else None, "temperatures": temps,
             "val_and_ckpt_s": round(time.time() - tv, 1), "ckpt": name}
        tr["history"].append(h)
        log("epoch", h)
        if best is None or vce < best["val_ce"]:
            best, bad = h, 0
        else:
            bad += 1
        for d in glob.glob(os.path.join(WORK, "ckpt_ep*")):
            if os.path.basename(d) not in (best["ckpt"], name):
                shutil.rmtree(d, ignore_errors=True)
        tr.update({"epochs_done": round(ep_done, 3), "best_epoch": best["epoch"],
                   "states_per_s": tr["rows_seen"] / len(QIDS) / tr["step_s"], "rows_per_s": tr["rows_seen"] / tr["step_s"]})
        M["wall"]["train_s"] = round(time.time() - t, 1)
        save_metrics()
        if stop:
            break
        if bad > C["patience"]:
            tr["stop_reason"] = "early stopping (val CE)"
            break
    tr["epochs_done"] = round(ep_done, 3)
    M["wall"]["train_s"] = round(time.time() - t, 1)
    del opt, sched, scaler, fwd, model
save_metrics()
del agent
gc.collect(); torch.cuda.empty_cache()

# ---------------------------------------------------------------- phase 3: trained eval
if best:
    t = time.time()
    ta = laya.load(os.path.join(WORK, best["ckpt"]), device="cuda")
    tr["eval_ckpt"] = best["ckpt"]
    for s in EVAL_SETS:
        M["models"]["trained"][s] = eval_agent(ta, SETS[s], tag="trained__" + s)
        save_metrics()
    if SETS["real_test"]:
        base = M["models"]["trained"]["real_test"]["macro_f1"]
        M["robustness"] = {"base_macro_f1": base}
        for v in ("extra_field", "shuffled_keys", "renamed_key"):
            m = eval_agent(ta, SETS["real_test"], variant=v, tag="trained__real_test__" + v)
            M["models"]["trained"]["real_test__" + v] = m
            M["robustness"][v] = {"macro_f1": m["macro_f1"],
                                  "drop": None if base is None or m["macro_f1"] is None else base - m["macro_f1"]}
            save_metrics()
    M["wall"]["trained_eval_s"] = round(time.time() - t, 1)
    del ta
else:
    M["train"]["stop_reason"] = M["train"].get("stop_reason") or "no training data or no time"

if not C["keep_ckpt"]:
    for d in glob.glob(os.path.join(WORK, "ckpt_ep*")):
        shutil.rmtree(d, ignore_errors=True)
else:
    for d in glob.glob(os.path.join(WORK, "ckpt_ep*")):
        if best and os.path.basename(d) != best["ckpt"]:
            shutil.rmtree(d, ignore_errors=True)
M["status"] = "done"
save_metrics()
log("done; budget used %.1f / %.1f min" % (elapsed() / 60, C["time_budget_min"]))
