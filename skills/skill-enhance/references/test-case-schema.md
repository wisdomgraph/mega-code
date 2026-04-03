# Test Case Schema & Guidelines

Generate exactly 4 realistic test cases to evaluate this skill.

## JSON Schema

```json
{
  "cases": [
    {
      "task": "A realistic coding task prompt that this skill should help with",
      "expectations": [
        {"text": "Specific, verifiable assertion about what a good response should include"},
        {"text": "Another specific assertion"},
        {"text": "A third assertion"}
      ]
    }
  ]
}
```

## Guidelines

- Each `task` should be a realistic coding request written as a real user would — concrete, specific, with enough detail to get a meaningful response
- Each test case should have 3-4 `expectations` — specific, verifiable natural language assertions
- Expectations should distinguish a response that follows the skill from one that doesn't
- Focus on observable behaviors and concrete outputs, not vague quality claims
- Good: "The commit message follows conventional commit format with a type prefix like feat: or fix:"
- Bad: "The response is good quality"
