# Email Templates

## Choosing the template

Set it per query in the **Email template** field:

| Field value | Template used |
|-------------|---------------|
| *(empty)* or `{default}` | `templates/email/default.html` |
| `{custom}` | `templates/email/custom.html` (letters, digits, `-` and `_` only) |
| HTML code | The HTML itself |

If a template cannot be used (missing file, syntax error), the email goes out with the default template.

## Variables

Templates use [Jinja](https://jinja.palletsprojects.com/templates/) and see these variables:

| Variable | Content |
|----------|---------|
| `query_name` | Query name |
| `query_description` | Query description |
| `check_time` | Time of the check (UTC) |
| `error_count` | Number of errors |
| `errors` | The errors: a list of rows, each a dictionary column → value |
| `columns` | Column names, in query order |
| `email_type` | `new_errors` or `reminder` |

Values are escaped automatically. Templates run in a sandbox: they cannot reach Python objects, the configuration or files.

```jinja
<h2>{{ query_name }}: {{ error_count }} errors</h2>
<p>Checked on {{ check_time|localtime('%d/%m/%Y %H:%M') }}</p>
<table>
  <tr>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr>
  {% for e in errors %}
  <tr>{% for c in columns %}<td>{{ e[c] }}</td>{% endfor %}</tr>
  {% endfor %}
</table>
```

`localtime` converts a date to the time zone set in `TIMEZONE`.

## Subject

The **Email subject** field accepts `{query_name}` and `{error_count}`. Reminders get `[REMINDER]` in front.
