# Email Templates

## Choosing the template

Set it per query in the **Email template** field:

| Field value | Template used |
|-------------|---------------|
| *(empty)* or `{default}` | `templates/email/default.html` |
| `{custom}` | `templates/email/custom.html` (letters, digits, `-` and `_` only) |
| HTML code | The HTML itself, also when it starts and ends with Jinja code such as `{% if ... %}` |

If a template cannot be used (missing file, syntax error), the email goes out with the default template.

**Send sample email**, under the template in the query form, sends the email as the form is now, saved or not, with the rows of **Test query** or the latest errors of the query, to the addresses you type. A template that cannot be used is reported there instead.

## Variables

Templates use [Jinja](https://jinja.palletsprojects.com/templates/) and see these variables:

| Variable | Content |
|----------|---------|
| `query_name` | Query name |
| `query_description` | Query description |
| `check_time` | Time of the check (UTC) |
| `error_count` | Number of errors, also those not listed |
| `errors` | The errors: a list of rows, each a dictionary column → value; the first `EMAIL_MAX_ROWS` |
| `errors_not_listed` | How many errors are beyond `EMAIL_MAX_ROWS`, not in `errors` (`0` when they are all there) |
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
{% if errors_not_listed %}<p>+ {{ errors_not_listed }} more</p>{% endif %}
```

`localtime` converts a date to the time zone set in `TIMEZONE`.

## Subject

The **Email subject** field accepts `{query_name}` and `{error_count}`, also with a format such as `{error_count:03d}`. Anything else between braces is sent as written, and saving the query points it out. An empty subject is `[ErrorEngine] New errors: {query_name}`. Reminders get `[REMINDER]` in front, unless the subject already says REMINDER.
