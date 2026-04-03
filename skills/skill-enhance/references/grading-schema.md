# Grading Schema & Rules

You are a strict evaluation grader. For each test case, grade BOTH the with-skill and baseline outputs against every expectation.

## Process

For each entry in the A/B results:
1. Read the `task`, `expectations`, `with_skill_output`, and `baseline_output`
2. Grade the **with-skill output** against each expectation
3. Grade the **baseline output** against each expectation
4. For each expectation: determine `passed` (true/false) and provide `evidence` (one sentence)

Be strict: only pass if the expectation is clearly and unambiguously met. If vague or partial, mark as failed.

## JSON Schema

```json
[
  {
    "with_skill_gradings": [
      {"expectation": "...", "passed": true, "evidence": "The output includes..."},
      {"expectation": "...", "passed": false, "evidence": "The output does not mention..."}
    ],
    "baseline_gradings": [
      {"expectation": "...", "passed": false, "evidence": "No evidence of..."},
      {"expectation": "...", "passed": true, "evidence": "The output does include..."}
    ]
  }
]
```
