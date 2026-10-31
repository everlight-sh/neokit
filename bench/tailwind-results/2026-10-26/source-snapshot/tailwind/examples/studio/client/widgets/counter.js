export function mount(root) {
  const output = root.querySelector("[data-count]");
  const add = root.querySelector("[data-increment]");
  const reset = root.querySelector("[data-reset]");
  const bars = [...root.querySelectorAll(".counter-bars i")];
  let count = Number(output.textContent) || 0;
  function render() {
    output.textContent = String(count);
    bars.forEach((bar, i) => bar.classList.toggle("filled", i < count % 11));
  }
  const increment = () => { count = Math.min(count + 1, 1000000); render(); };
  const clear = () => { count = 0; render(); };
  add.addEventListener("click", increment);
  reset.addEventListener("click", clear);
  render();
  return () => { add.removeEventListener("click", increment); reset.removeEventListener("click", clear); };
}
