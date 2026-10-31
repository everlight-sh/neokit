export function mount(root) {
  const input = root.querySelector("[data-search]");
  const rows = [...root.querySelectorAll("[data-note]")];
  const status = root.querySelector("[data-filter-status]");
  function filter() {
    const query = input.value.trim().toLowerCase();
    let count = 0;
    for (const row of rows) {
      row.hidden = !(`${row.dataset.note} ${row.textContent}`.toLowerCase().includes(query));
      if (!row.hidden) count++;
    }
    status.textContent = `${count} ${count === 1 ? "note" : "notes"} on the workbench`;
  }
  input.addEventListener("input", filter);
  return () => input.removeEventListener("input", filter);
}
