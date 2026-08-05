# Eval

Destination for the eval harness (build-plan: "adapt an eval harness from camp's
9-question cli and gav's harness"). Needs a deployed endpoint and an account, so
nothing lands here until after the first deploy. First jobs once it exists:
retune `retrieval.min_score` (0.35 was tuned against a differently shaped corpus)
and run Student Affairs' 5-10 query test set.
