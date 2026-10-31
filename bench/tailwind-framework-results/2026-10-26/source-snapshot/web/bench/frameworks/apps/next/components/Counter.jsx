"use client";
import { useState } from "react";

export default function Counter() {
  const [count, setCount] = useState(0);
  return <section className="counter" data-web-widget="counter"><output data-count="" aria-label="Counter value" aria-live="polite">{count}</output><button type="button" data-increment="" onClick={() => setCount(count + 1)}>Add one +</button><button type="button" data-reset="" onClick={() => setCount(0)}>Reset</button></section>;
}
