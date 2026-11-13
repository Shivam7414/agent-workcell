// The chat page. Every piece of text from the model, the data or the person is shown with textContent. The one
// exception is the finished answer's answer_html, which the answer server has already cleaned (no images, no links,
// no attributes); the page's content security policy blocks outside sources as a second layer.
"use strict";

const conversation = document.getElementById("conversation");
const welcome = document.getElementById("welcome");
const askForm = document.getElementById("ask");
const questionBox = document.getElementById("question");
const sendButton = document.getElementById("send");
const notesList = document.getElementById("notes");
// One conversation per page: follow-up questions continue it, and reloading the page starts a new one. The answer
// server keys it by the signed-in person, so this id names a conversation but grants nothing.
const conversationId = crypto.randomUUID();
const SVG = "http://www.w3.org/2000/svg";

// What each role may see. This only describes the access; the database enforces it.
const SCOPES = {
  admin: () => ["Administrator", "You can see all purchasing data: every order, delivery, invoice and payment."],
  buyer: () => ["Buyer", "You can see your own purchase orders, and the deliveries and invoices for them. " +
                          "Payments are not visible to buyers."],
  supplier_user: (org) => [`Supplier user at ${org}`, `You can see the orders Meridian Works has sent to ${org}, ` +
                                                       "and your own invoices and payments."],
};
const ENFORCED = " The database enforces this, whatever you ask.";

const SUGGESTIONS = {
  admin: ["Which suppliers are On Hold?", "What is the total amount of all overdue invoices?",
          "Which department ordered the most in 2026?", "What do the notes on purchase order 4500000007 say?"],
  buyer: ["How many of my purchase orders are Sent to Supplier?", "Which invoices for my orders are overdue?",
          "What is the total value of my orders that are Received or Closed?",
          "Give me an Excel file of my orders that are still open."],
  supplier_user: ["How much has Meridian Works paid us so far?", "Which of our invoices were rejected, and why?",
                  "Which of our orders are Partially Received?", "Export our invoices that are not paid yet to a CSV file."],
};

const STEP_WORDS = {
  knowledge: "Read the data guide",
  get_schema: "Checked table descriptions",
  run_query: "Ran a query",
  create_file: "Built a file",
  remember: "Saved a note",
  search_documents: "Searched documents",
  read_document: "Read a document",
};

// The sources event comes from the answer server, built from the database rows the document tools returned. Every
// value is shown with textContent: titles and sections are text from documents.
function sourcesList(data) {
  const details = element("details", "sources");
  const cited = data.documents.filter((document) => document.cited).length;
  details.open = cited > 0;
  details.append(element("summary", "", `Sources: ${data.documents.length} documents read, ${cited} cited`));
  const list = element("ul");
  for (const document of data.documents) {
    const item = element("li", document.cited ? "cited" : "");
    item.append(element("strong", "", `${document.doc_number} ${document.title}`));
    const places = document.passages.map((passage) => [
      passage.page !== null ? `page ${passage.page}` : null,
      passage.section ? `"${passage.section}"` : null,
      `passage ${passage.passage}`,
    ].filter(Boolean).join(", "));
    item.append(element("span", "", ` (version ${document.version_no}): ${places.join("; ")}`));
    list.append(item);
  }
  details.append(list);
  return details;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(paths) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of paths) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function scrollDown() {
  conversation.scrollTop = conversation.scrollHeight;
}

async function postJson(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
    credentials: "same-origin",
  });
}

async function errorText(response) {
  try {
    return (await response.json()).error || "The answer could not be completed. Try again.";
  } catch {
    return "The answer could not be completed. Try again.";
  }
}

// Server-sent events, read from a fetch response (EventSource cannot send a POST body).
async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let type = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7);
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (data.length) yield { type, data: JSON.parse(data.join("\n")) };
    }
  }
}

function fileLink(file) {
  const link = element("a", "file");
  link.href = "/api/files/" + encodeURIComponent(file.file_id);
  link.setAttribute("download", "");
  link.append(icon(["M12 4v11", "M7 10l5 5 5-5", "M5 20h14"]), element("span", "", file.name));
  link.append(element("span", "file-rows", `${file.rows} ${file.rows === 1 ? "row" : "rows"}`));
  return link;
}

function waiting() {
  const dots = element("div", "waiting");
  dots.setAttribute("aria-label", "Working");
  dots.append(element("span"), element("span"), element("span"));
  return dots;
}

async function ask(question) {
  welcome.hidden = true;
  const turn = element("div", "turn person");
  turn.append(element("p", "bubble", question));
  conversation.append(turn);

  const reply = element("article", "turn assistant");
  const steps = element("ul", "steps");
  const draft = element("div", "draft");
  const files = element("div", "files");
  const dots = waiting();
  reply.append(steps, dots, draft, files);
  conversation.append(reply);
  scrollDown();

  const queries = [];
  let sources = null;
  const started = performance.now();
  const response = await postJson("/api/ask", { question, conversation_id: conversationId });
  if (!response.ok) {
    if (response.status === 401) window.location.assign("/login");
    dots.remove();
    draft.textContent = await errorText(response);
    reply.classList.add("failed");
    return;
  }
  for await (const event of readEvents(response)) {
    const data = event.data;
    if (event.type === "text") {
      draft.textContent += data.text;
    } else if (event.type === "tool_call") {
      steps.append(element("li", "", STEP_WORDS[data.name] || "Worked on the data"));
      if (data.arguments && typeof data.arguments.sql === "string") queries.push(data.arguments.sql);
    } else if (event.type === "file") {
      files.append(fileLink(data));
    } else if (event.type === "note") {
      steps.append(element("li", "note", "Saved a note: " + data.text));
      loadNotes();
    } else if (event.type === "sources") {
      sources = sourcesList(data);
    } else if (event.type === "finished") {
      dots.remove();
      if (data.ok) {
        const answer = element("div", "answer");
        answer.innerHTML = data.answer_html; // cleaned by the answer server; see the comment at the top
        draft.replaceWith(answer);
        reply.classList.add("done");
      } else {
        draft.textContent = data.error || "The answer could not be completed. Try again.";
        reply.classList.add("failed");
      }
      if (sources) reply.append(sources);
      if (queries.length) {
        const details = element("details", "queries");
        details.append(element("summary", "", `Queries behind this answer (${queries.length})`));
        details.append(element("pre", "", queries.join("\n\n")));
        reply.append(details);
      }
      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      reply.append(element("p", "answer-meta", `Answered in ${seconds} s`));
    }
    scrollDown();
  }
}

async function loadNotes() {
  const response = await fetch("/api/notes", { credentials: "same-origin" });
  notesList.replaceChildren();
  if (!response.ok) return;
  const { notes } = await response.json();
  if (!notes.length) notesList.append(element("li", "empty", "No notes yet."));
  for (const note of notes) notesList.append(element("li", "", note.text));
}

async function loadPerson() {
  const response = await fetch("/api/me", { credentials: "same-origin" });
  if (response.status === 401) return window.location.assign("/login");
  const me = await response.json();
  const scope = (SCOPES[me.role] || (() => ["", ""]))(me.organisation || "your company");
  document.getElementById("who-name").textContent = me.name;
  document.getElementById("who-detail").textContent = scope[0];
  document.getElementById("scope-title").textContent = scope[0].endsWith(".") ? scope[0] : scope[0] + ".";
  document.getElementById("scope-text").textContent = scope[1] + ENFORCED;
  document.getElementById("greeting").textContent = `What would you like to know, ${me.name.split(" ")[0]}?`;
  const suggestions = document.getElementById("suggestions");
  for (const text of SUGGESTIONS[me.role] || []) {
    const button = element("button", "suggestion", text);
    button.type = "button";
    button.addEventListener("click", () => submit(text));
    suggestions.append(button);
  }
}

function fitBox() {
  questionBox.style.height = "auto";
  questionBox.style.height = Math.min(questionBox.scrollHeight, 192) + "px";
}

async function submit(question) {
  question = question.trim();
  if (!question || sendButton.disabled) return;
  questionBox.value = "";
  fitBox();
  sendButton.disabled = true;
  try {
    await ask(question);
  } catch {
    conversation.append(element("div", "turn assistant failed", "The connection was lost. Check that the server is running and try again."));
  } finally {
    sendButton.disabled = false;
    questionBox.focus();
  }
}

askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submit(questionBox.value);
});

questionBox.addEventListener("input", fitBox);
questionBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    askForm.requestSubmit();
  }
});

document.getElementById("clear-notes").addEventListener("click", async () => {
  await postJson("/api/notes/clear");
  loadNotes();
});

loadPerson();
loadNotes();
