// The only plain JavaScript of the app; everything else is HTMX attributes in the templates.

// The ✕ button of an order line removes its row.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-row]");
  if (button) button.closest("tr").remove();
});

// A select that adds a row goes back to its first option, so the same choice can be added again.
document.addEventListener("htmx:afterRequest", (event) => {
  if (event.target.matches("select[data-reset]")) event.target.value = "";
});

// Enter in a search field inside a form searches instead of sending the form.
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.matches("[data-no-submit]")) event.preventDefault();
});
