// Local counter widget for the catalog fixture.
export function mount(root) {
  const output = root.querySelector("[data-count]");
  const add = root.querySelector("[data-increment]");
  const reset = root.querySelector("[data-reset]");
  let count = 0;
  const render = () => { output.textContent = String(count); };
  const increment = () => { count++; render(); };
  const clear = () => { count = 0; render(); };
  add.addEventListener("click", increment);
  reset.addEventListener("click", clear);
  return () => { add.removeEventListener("click", increment); reset.removeEventListener("click", clear); };
}
