"""Documents: uploaded files, read into passages that the agent can search and cite.

    read.py     plain code per file format: the file's text as blocks, each with its section and page
    chunk.py    blocks into passages of bounded size, keeping the section and page for citations
    embed.py    embeddings from OpenAI or OpenRouter, chosen in settings, and a free fake embedder for tests
    search.py   hybrid search by words and meaning, on the person's guarded connection
    store.py    the install SQL for the documents role and the job table, the start-up check, upload and the worker

The passages go into the business database's document tables, so the data guard decides who reads them, as it does
for every other table. The design is in docs/architecture.md ("Documents").
"""
