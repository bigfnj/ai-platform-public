window.addEventListener("DOMContentLoaded", async () => {
  // Live app renders worksheet PDFs with PDF.js. Bundled/offline copies ship
  // pre-rendered JPGs listed in worksheets.json, so PDF.js (vendor/) may be absent.
  if (typeof pdfjsLib !== "undefined") {
    pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js";
  }

  let WS = null;
  try {
    const r = await fetch("worksheets.json");
    if (r.ok) WS = await r.json();
  } catch (e) { /* no manifest — live PDF.js mode */ }

  async function renderWorksheet(host) {
    if (host.dataset.rendered) return;
    host.dataset.rendered = "loading";
    const key = decodeURI(host.dataset.pdf || "");

    // Bundle mode: pre-rendered images.
    if (WS && WS[key]) {
      host.innerHTML = WS[key].map((src, i) =>
        `<img class="wsPage" src="${src}" alt="Worksheet page ${i + 1}" loading="lazy" ` +
        `style="width:100%;display:block;margin-bottom:6px">`).join("");
      host.dataset.rendered = "ready";
      return;
    }

    // No PDF.js and no images — offer the original file.
    if (typeof pdfjsLib === "undefined") {
      host.innerHTML = `<p class="pdfError"><a href="${host.dataset.pdf}" target="_blank">Open the worksheet</a></p>`;
      host.dataset.rendered = "error";
      return;
    }

    // Live mode: render the PDF with PDF.js.
    try {
      const pdf = await pdfjsLib.getDocument(host.dataset.pdf).promise;
      host.innerHTML = "";
      for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber++) {
        const page = await pdf.getPage(pageNumber);
        const base = page.getViewport({ scale: 1 });
        const available = Math.max(600, host.clientWidth || 900);
        const viewport = page.getViewport({ scale: available / base.width });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.setAttribute("aria-label", `Worksheet page ${pageNumber}`);
        host.appendChild(canvas);
        await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      }
      host.dataset.rendered = "ready";
    } catch (error) {
      host.innerHTML = `<p class="pdfError">The worksheet could not render. <a href="${host.dataset.pdf}" target="_blank">Open the original PDF</a>.</p>`;
      host.dataset.rendered = "error";
    }
  }

  const observer = new MutationObserver(() => document.querySelectorAll(".pdfPages").forEach(renderWorksheet));
  observer.observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll(".pdfPages").forEach(renderWorksheet);
});
