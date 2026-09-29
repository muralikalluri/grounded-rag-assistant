# Loom script (about 3 minutes)

1. **Pitch (15 s).** "Ask questions about your own documents; every answer quotes the source, and if the
   documents don't say, it says 'not found'. All data here is fictional."
2. **Sign in as Alice (20 s).** Show single sign-on (Keycloak). Alice is in HR.
3. **Cited answer (45 s).** Ask "What is the salary band for L5 engineers?". Click a citation: the PDF page opens
   with the quoted text highlighted.
4. **Same question as Bob (30 s).** Sign out, sign in as Bob (Engineering). The answer is "not found": the HR
   document is not just hidden from the answer, it is never retrieved.
5. **Not in the documents (20 s).** Ask "What is the company policy on working remotely from abroad?".
   Result: "not found" instead of a guess.
6. **Evidence (30 s).** Open the Results table in the README. Say that it is generated from the eval report, what
   was and was not measured (state honestly whether generation metrics were run), and that the confidence
   intervals are wide at 30 questions.
7. **Deploy and handover (20 s).** Show `infra/azure` and `sample-deliverable/POC_HANDOVER.md`; say what is
   untested (Azure apply).

Record after `make up && make ingest-demo`, with an LLM key set. Do not show `.env`.
