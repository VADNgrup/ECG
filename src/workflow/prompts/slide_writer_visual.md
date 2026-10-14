<visual>
Some slides also have `shows`: what the slide may draw. `formula`: the formulas of its passages, `code`: its listing,
each with an `[S#]` label cited like a sentence; a table or image captions.
- A formula or code in `shows` is explained (what it computes or does, its terms or steps, calls and results) by
  bullets that cite its S#, never copied into them. A formula is drawn only when a bullet cites it: cite one only when
  it is whole, readable and about what the bullets teach, never one that lost or scrambled terms (symbols run together,
  a split matrix, a side missing) or is only labels or numbers.
- `arrange` (one more field of the slide's answer, e.g. `"arrange": {"S7": ["x + y ≤ 4", "x ≥ 0"]}`): for a formula your
  bullets cite whose separate equations or constraints run together on one line, its lines as they should be drawn:
  the same text split only at spaces between whole equations, every character kept in order, nothing added, removed or
  reworded. Leave out every other formula.
</visual>
