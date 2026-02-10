# Routing Rules

Route notifications to different recipients based on error content.

## Example

```
If WAREHOUSE contains "EU"  →  warehouse-eu@company.com
If PRIORITY equals "HIGH"   →  urgent@company.com, manager@company.com
Default                      →  support@company.com
```

## Available Operators

| Operator | Description |
|----------|-------------|
| `equals` | Exact match |
| `not_equals` | Does not match |
| `contains` | Contains substring |
| `not_contains` | Does not contain |
| `startswith` | Starts with |
| `endswith` | Ends with |
| `in` | Value in comma-separated list |
| `not_in` | Value not in list |
| `gt` | Greater than |
| `gte` | Greater than or equal |
| `lt` | Less than |
| `lte` | Less than or equal |
| `is_empty` | Field is empty/null |
| `is_not_empty` | Field has value |
| `regex` | Matches regex pattern |
| `not_regex` | Does not match regex |

## Setup

1. Open a query → click **Modifica** (Edit)
2. Scroll to **Routing Condizionale** (Conditional Routing)
3. Enable the routing toggle
4. Click **Aggiungi Regola** (Add Rule)
5. Configure:
   - Field name (must match a column from your query)
   - Operator
   - Value to match
   - Recipients (comma-separated emails)
6. Set priority (lower = evaluated first)
7. Enable "Stop on match" if needed
8. Save

## Logic

- Rules are evaluated in priority order (lowest first)
- Multiple conditions per rule can use AND/OR logic
- "Stop on match" prevents further rule evaluation
- Default recipients are used when no rules match
- If "No match action" is set to "Skip", unmatched errors won't trigger notifications
