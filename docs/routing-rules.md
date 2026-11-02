# Routing Rules

Send each error to different recipients depending on its content.

## Example

```
If WAREHOUSE contains "EU"   →  warehouse-eu@company.com
If PRIORITY equals "HIGH"    →  urgent@company.com, manager@company.com
No rule matches              →  support@company.com
```

## Setup

Routing is in the query form (**Edit**), in **Conditional routing**: it appears after the query has been saved once.

1. Turn on **Conditional routing**
2. Set the **Default recipients** and what happens **When no rule matches**: send to the default recipients, or don't send
3. **Add rule**: name, recipients, logic (all conditions or at least one), priority
4. Add the conditions: field (a column of the query), operator, value; **Aa** makes the comparison case-sensitive
5. **Save rule**

A rule can be paused by unticking **Rule active**. **Stop on match** skips the rules after it.

## How rules are applied

- Rules are checked by priority, lowest number first
- Every rule that matches adds its recipients, until a rule with **Stop on match**
- A rule without conditions always matches
- With routing on, the query's **Recipients** field is not used: emails go to the rules' recipients and to the default recipients
- Routing applies to email only: notification channels receive every error

## Operators

| Operator | Matches when the field |
|----------|------------------------|
| `equals` | is equal to the value |
| `not_equals` | is different from the value |
| `contains` | contains the value |
| `not_contains` | does not contain the value |
| `startswith` | starts with the value |
| `endswith` | ends with the value |
| `in` | is one of the comma-separated values |
| `not_in` | is none of the comma-separated values |
| `gt` | is greater than the number |
| `gte` | is greater than or equal to the number |
| `lt` | is less than the number |
| `lte` | is less than or equal to the number |
| `is_empty` | is empty or missing |
| `is_not_empty` | has a value |
| `regex` | matches the regular expression |
