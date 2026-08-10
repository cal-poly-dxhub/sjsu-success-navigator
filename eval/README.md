# Eval

Destination for the eval harness (build-plan: "adapt an eval harness from camp's
9-question cli and gav's harness"). Needs a deployed endpoint and an account, so
the harness lands here after the first deploy. First jobs once it exists:
retune `retrieval.min_score` (0.35 was tuned against a differently shaped corpus)
and run Student Affairs' 5-10 query test set.

## ground-truth.yaml

The fixed baseline the harness scores against: 82 question/answer pairs known
correct INDEPENDENTLY of the program - sourced from the sponsor resource sheets
and the challenge brief, with every load-bearing fact verified against the live
public pages on 2026-08-10 (raw HTML, not our pipeline). The file header
documents the schema, the scoring guidance, and the sponsor-sheet errors found
during verification (do not "fix" entries back to the sheets' dead URLs or
stale contacts).

Composition: 36 routing, 17 factual, 8 process, 5 disambiguation, 6
safety-intercept (the safety panel MUST appear, model-triaged with
server-resolved contacts; a fluent reply without it is a failure), 5 honest-gap
(the corpus has no page; inventing one is a failure), 5 out-of-scope (nothing
to do with SJSU; answering the question is the failure, declining is the pass).
21 pairs are sensitivity-flagged and carry a tone rubric on top of fact checks.

## Running it

`run_eval.py` sends the questions to the real deployed endpoint (API Gateway +
Cognito, the frontend's exact path) and records every wire response verbatim in
`results/eval-<stamp>.json`, then renders `results/eval-<stamp>.html` - golden
expectation beside the system's actual answer, one block per question. NO
SCORING lives in either script; accuracy is judged by humans (or by Claude
reading the transcript), and judgments recorded in
`judgments/<transcript stem>.yaml` fold into the page on re-render.

```
python3 -m pip install boto3 httpx PyYAML     # eval-only deps, not pinned by the app
export EVAL_PASSWORD=<shared pilot password>  # or --password-file OUTSIDE the repo
python3 eval/run_eval.py                      # all 77, ~5 min, needs the gavilan AWS profile
python3 eval/run_eval.py --ids 'safety-*'     # subset
python3 eval/render_results.py                # re-render newest transcript (after judging)
```

Endpoint and Cognito client id are discovered from the SjsuNavigatorStack
outputs; nothing is hardcoded. Transcripts are kept - they are the before/after
record across corpus and prompt changes.
