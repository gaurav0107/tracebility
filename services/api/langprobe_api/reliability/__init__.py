"""Eval-reliability — the rigor that makes evals trustworthy.

PoLL + Luna give you judges. This package answers "can you trust them?":
schema-adherence (do outputs parse), test-retest (are scores stable on repeat),
inter-judge agreement (does the panel concur). Pure metrics over the existing
eval_score store — no new write path.
"""
