// The sign-in page: an account button fills the form with that account and the reference ERP's shared password.
"use strict";

const SHARED_PASSWORD = "workcell-demo"; // every seeded user of the reference ERP has it; see reference_erp/README.md

for (const button of document.querySelectorAll("button.account")) {
  button.addEventListener("click", () => {
    document.getElementById("email").value = button.dataset.email;
    document.getElementById("password").value = SHARED_PASSWORD;
    for (const other of document.querySelectorAll("button.account")) {
      other.setAttribute("aria-pressed", other === button ? "true" : "false");
    }
    document.querySelector(".signin-form button[type=submit]").focus();
  });
}
