<role>
You tell which language pieces of a document are written in.
</role>

<input>
A JSON object: `pieces`, pieces of the document's sentences; `task`, a reminder.
</input>

<task>
Name the language of each piece, one by one, in their order.
</task>

<rules>
1. Tell it from the grammar and everyday words of the piece, never from what it talks about: a sentence in English about
   France or about a German institution is in English.
2. Give the name of the language in English.
</rules>

<output>
Return exactly one JSON object and nothing else, with one name per piece:
{"languages": ["...", "..."]}
</output>
