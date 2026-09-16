// A deliberately small Markdown renderer for the reports this app generates.
// Everything is escaped first; only the constructs the renderer itself emits are turned into
// markup, so a malicious source title cannot inject HTML.

const escapeHtml = (text) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function inline(text) {
  let out = escapeHtml(text);
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  out = out.replace(/((?:\[\d+\])+)/g, (group) =>
    group.replace(/\[(\d+)\]/g, '<a class="cite" href="#src-$1">[$1]</a>'));
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])_([^_]+)_(?=[\s).,;:]|$)/g, "$1<em>$2</em>");
  return out;
}

export function renderMarkdown(source) {
  const lines = source.split("\n");
  const html = [];
  let list = null;
  let quote = [];
  let paragraph = [];
  let inSources = false;

  const flushParagraph = () => {
    if (paragraph.length) html.push(`<p>${inline(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (list) html.push(`<${list.tag}>${list.items.join("")}</${list.tag}>`);
    list = null;
  };
  const flushQuote = () => {
    // A quote that opens in bold is the gate's failure banner.
    const alert = quote.length && /^\*\*/.test(quote[0]) ? ' class="alert"' : "";
    if (quote.length) html.push(`<blockquote${alert}>${quote.map(inline).join("<br>")}</blockquote>`);
    quote = [];
  };
  const flushAll = () => { flushParagraph(); flushList(); flushQuote(); };

  for (const raw of lines) {
    const line = raw.trimEnd();
    let match;
    if (!line.trim()) { flushAll(); continue; }
    if ((match = line.match(/^(#{1,3})\s+(.*)$/))) {
      flushAll();
      const level = match[1].length;
      inSources = /^(sources|kaynaklar)$/i.test(match[2].trim());
      html.push(`<h${level}>${inline(match[2])}</h${level}>`);
    } else if ((match = line.match(/^>\s?(.*)$/))) {
      flushParagraph(); flushList();
      quote.push(match[1]);
    } else if ((match = line.match(/^(\d+)\.\s+(.*)$/))) {
      flushParagraph(); flushQuote();
      if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
      const anchor = inSources ? ` id="src-${match[1]}"` : "";
      list.items.push(`<li${anchor}>${inline(match[2])}</li>`);
    } else if ((match = line.match(/^[-*]\s+(.*)$/))) {
      flushParagraph(); flushQuote();
      if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
      list.items.push(`<li>${inline(match[1])}</li>`);
    } else {
      flushList(); flushQuote();
      paragraph.push(line.trim());
    }
  }
  flushAll();
  return html.join("\n");
}
