# Teaching Haze's offline AI

Haze's copilot already learns without training: its instructions (what each
zone is for, what looks good), the notes it keeps, and what it learns from
your changes to its shows.  This folder is the next step: teaching the
**offline AI** itself, on your own examples.

## 1. Collect examples (just use the desk)

Every copilot request is written down on your computer (`data/ai_examples.jsonl`).
When you press **Keep** on what it did, or 👍 on an answer, that's a good
example; **Throw away** / 👎 is a bad one.  Settings → AI → *Teaching the
offline AI* shows how many good ones there are.

You want **a few hundred** good examples, across everything you do: shows,
quick looks, questions, the room, buttons.

## 2. Measure first: the practice exam

    python tools/aiexam.py

Forty real requests, each scored by what a good answer must and must not do
(`tools/aiexam/cases.json` - add your own).  It runs on scratch desks, never
your shows, and writes `data/aiexam/report-<date>.json`.  Run it before and
after any change, and keep only what scores better.

## 3. Train (a PC with an NVIDIA card)

1. Settings → AI → *Teaching the offline AI* → **Save the training file**
   (`data/ai_training.jsonl`).
2. On a Linux PC or WSL2 with an NVIDIA card (8 GB for 8B, 16 GB for 14B):

       cd tools/finetune
       pip install -r requirements.txt
       python train.py --data ../../data/ai_training.jsonl

   About 20-60 minutes for a few hundred examples.
3. Settings → AI → **AI pack**: pick the `haze-tuned-8b…gguf` it made, and
   switch the copilot to **Local**.
4. Run the practice exam again.  Better: keep it.  Worse: switch back to the
   original model (it's still there).

## What stays private

The examples never leave your computer unless you copy them.  Pictures are
left out, and your AI key is never in them.
