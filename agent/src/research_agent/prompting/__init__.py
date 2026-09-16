"""Prompts as versioned artefacts (architecture v0.6 §20).

* A **signature** (code) fixes what an LLM step takes and returns: input variables, a Pydantic
  output model, a model tier. Node code never contains prompt text.
* A **prompt version** (YAML seed or Langfuse) supplies the words: instructions, a template and
  optional demos. It is accepted only if its declared output-schema hash matches the code, so a
  prompt edited in the Langfuse UI cannot break a node's contract.
* **Skills** add domain guidance and trusted domain lists, chosen per question.
"""
