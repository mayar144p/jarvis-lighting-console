"""Teach Haze's offline AI your way of working (a LoRA fine-tune).

Needs a PC with an NVIDIA graphics card (8 GB for the 8B model, 16 GB for
14B), Linux or WSL2, and the training file the desk makes (Settings -> AI ->
Teaching the offline AI -> Save the training file: data/ai_training.jsonl).

    pip install -r requirements.txt
    python train.py --data ../../data/ai_training.jsonl            # Qwen3 8B
    python train.py --data ../../data/ai_training.jsonl --model 14b

What comes out: haze-tuned-<model>-Q4_K_M.gguf - load it in the desk with
Settings -> AI -> AI pack (the same place a downloaded model goes).  The
original model is untouched: switch back any time.

Then run the practice exam (python tools/aiexam.py) with the new model and
compare the score with the last report: keep the new one only if it's better.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

BASE = {"8b": "unsloth/Qwen3-8B-unsloth-bnb-4bit", "14b": "unsloth/Qwen3-14B-unsloth-bnb-4bit"}


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        msgs = r.get("messages") or []
        if len(msgs) >= 3 and msgs[-1].get("role") == "assistant":
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--model", choices=sorted(BASE), default="8b")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args()

    rows = load(args.data)
    if len(rows) < 50:
        raise SystemExit(f"only {len(rows)} usable examples - keep using the copilot (Keep / 👍) until there are a few hundred")
    random.seed(7)
    random.shuffle(rows)
    hold = max(5, len(rows) // 20)                 # a few kept back to see it learning, not memorising
    train, test = rows[hold:], rows[:hold]
    print(f"{len(train)} examples to learn from, {len(test)} kept back to check")

    from datasets import Dataset                    # noqa: E402 - heavy, only here
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

    model, tok = FastLanguageModel.from_pretrained(BASE[args.model], max_seq_length=8192, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32, lora_dropout=0.0,
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                                             "gate_proj", "up_proj", "down_proj"])

    def text(r):
        # the desk's own conversation: its instructions, the request, the AI's
        # tool calls and what the desk answered, and its reply
        return {"text": tok.apply_chat_template(r["messages"], tools=r.get("tools"), tokenize=False)}
    tr = Dataset.from_list(train).map(text, remove_columns=["messages"] + (["tools"] if "tools" in train[0] else []))
    te = Dataset.from_list(test).map(text, remove_columns=["messages"] + (["tools"] if "tools" in test[0] else []))
    trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=tr, eval_dataset=te,
                         args=SFTConfig(output_dir=str(args.out), num_train_epochs=args.epochs, learning_rate=1e-4,
                                        per_device_train_batch_size=1, gradient_accumulation_steps=8,
                                        warmup_ratio=0.05, logging_steps=5, eval_strategy="epoch",
                                        save_strategy="no", dataset_text_field="text", max_seq_length=8192,
                                        bf16=True, report_to="none"))
    trainer.train()
    name = f"haze-tuned-{args.model}"
    model.save_pretrained_gguf(str(args.out / name), tok, quantization_method="q4_k_m")
    got = sorted(args.out.glob(f"{name}*/*.gguf")) or sorted(args.out.glob("*.gguf"))
    print("\nDone." + (f" Load {got[-1]} in the desk: Settings -> AI -> AI pack." if got else ""))


if __name__ == "__main__":
    main()
