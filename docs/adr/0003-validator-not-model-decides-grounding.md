# 3. A deterministic validator decides what counts as grounded

Status: accepted

**Decision.** The model returns sentences with verbatim quotes; code verifies every quote and that
numbers, dates and negations appear in it. Unsupported sentences are dropped; an answer with none left
becomes "not found". A rerank-probability gate abstains before the LLM is called.

**Consequences.** Faithfulness does not depend on model obedience. Cost: some correct answers are
rejected when the model paraphrases instead of quoting (false abstention), which the eval measures.
The stage-1 threshold is tuned on a dev split only and biased low.
