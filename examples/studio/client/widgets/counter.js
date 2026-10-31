export function mount(root) {
  const output = root.querySelector("[data-count]");
  const add = root.querySelector("[data-increment]");
  const reset = root.querySelector("[data-reset]");
  let count = Number(output.textContent) || 0;
  const increment = () => { count = Math.min(count + 1, 1000000); output.textContent = String(count); };
  const clear = () => { count = 0; output.textContent = "0"; };
  add.addEventListener("click", increment);
  reset.addEventListener("click", clear);
  return () => { add.removeEventListener("click", increment); reset.removeEventListener("click", clear); };
}
