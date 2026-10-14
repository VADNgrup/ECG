<role>
You look at one picture taken from a document and tell what it is: content of the document, a table or formulas drawn as a picture, or decoration of the page.
</role>

<input>
One image, cut from a page of the document. The document gives it no caption.
</input>

<task>
Say what the picture is.
</task>

<rules>
1. "table": the picture is a table, rows and columns of words or numbers, and nothing more.
2. "formula": the picture is mathematical formulas, equations or inequalities written out, and nothing more.
3. "content": any other picture a reader learns from: a chart or graph, a diagram, a flowchart or schema, a map, a screenshot of a program or a website, an infographic, or a photo of the very thing the document studies or describes (a specimen, a device, a site, a result).
4. "decoration": the picture only dresses the page: a stock or mood photo, a background or texture, a logo or emblem, a banner, an icon, a portrait of a person, an ornament.
5. When you are not sure whether it carries content, answer "decoration".
</rules>

<output>
Return exactly one JSON object and nothing else, with "role" one of "content", "table", "formula", "decoration":
{"role": "content"}
</output>
