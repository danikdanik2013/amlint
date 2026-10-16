// Runs amlint in Pyodide, off the page's main thread so typing stays smooth.
// Messages in:  {id, cmd: "init", args: [wheel URL]} | {id, cmd: "check"|"route"|..., args}
// Messages out: {id, result} | {id, error} | {status} while loading.
// Nothing here sends the config anywhere: the only network requests are the Pyodide
// runtime and its packages from the CDN, and the amlint wheel from this site.

const PYODIDE_VERSION = "0.29.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

importScripts(PYODIDE_URL + "pyodide.js");

let api = null;
let loading = null;

async function init(wheel) {
  postMessage({ status: "Downloading Python (Pyodide " + PYODIDE_VERSION + ")…" });
  const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  postMessage({ status: "Installing amlint…" });
  // rich and pygments ship with Pyodide; amlint's own wheel comes from this site.
  await pyodide.loadPackage(["pyyaml", "rich", "pygments", new URL(wheel, self.location).href], {
    messageCallback: () => {},
  });
  const glue = await fetch(new URL("amlint_playground.py", self.location));
  if (!glue.ok) throw new Error(`amlint_playground.py: HTTP ${glue.status}`);
  pyodide.FS.writeFile("/home/pyodide/amlint_playground.py", await glue.text());
  api = pyodide.pyimport("amlint_playground");
  return { pyodide: PYODIDE_VERSION };
}

self.onmessage = async (event) => {
  const { id, cmd, args = [] } = event.data;
  try {
    if (cmd === "init") {
      loading = loading || init(args[0]);
      postMessage({ id, result: await loading });
      return;
    }
    await loading;
    postMessage({ id, result: JSON.parse(api[cmd](...args)) });
  } catch (err) {
    postMessage({ id, error: String(err && err.message ? err.message : err) });
  }
};
