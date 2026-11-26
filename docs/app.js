
document.addEventListener("DOMContentLoaded", async () => {
  const $ = id => document.getElementById(id);
  let items = [];
  let limit = 20;

  const filters = {
    law: "laws",
    topic: "topics",
    news_type: "news_type",
    kind: "kind"
  };

  const values = (item, key) =>
    [item[key] || []].flat();

  function render() {
    const q = $("q").value.toLowerCase().trim();

    const filtered = items.filter(item => {
      const text = JSON.stringify(item).toLowerCase();

      return text.includes(q) &&
        Object.entries(filters).every(([id, key]) =>
          !$(id).value ||
          values(item, key).includes($(id).value)
        );
    });

    $("items").replaceChildren();
    $("count").textContent = `Найдено: ${filtered.length}`;

    filtered.slice(0, limit).forEach(item => {
      const card = document.createElement("article");
      card.className = "card";

      const title = document.createElement("h3");
      const link = document.createElement("a");

      link.textContent = item.title;

      if (/^https?:\/\//i.test(item.url || "")) {
        link.href = item.url;
      }

      link.target = "_blank";
      link.rel = "noopener noreferrer";
      title.appendChild(link);

      const info = document.createElement("p");
      info.textContent = [
        item.source,
        item.published
          ? new Date(item.published).toLocaleDateString("ru-RU")
          : "",
        ...values(item, "laws"),
        item.news_type
      ].filter(Boolean).join(" • ");

      card.append(title, info);
      $("items").appendChild(card);
    });

    $("more").hidden = filtered.length <= limit;
  }

  try {
    const response = await fetch(
      "./news.json?t=" + Date.now(),
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }

    const data = await response.json();
    items = data.items || [];

    Object.entries(filters).forEach(([id, key]) => {
      const unique = [...new Set(
        items.flatMap(item => values(item, key))
      )].sort();

      unique.forEach(value => {
        if (![...$(id).options].some(o => o.value === value)) {
          $(id).add(new Option(value, value));
        }
      });

      $(id).addEventListener("change", () => {
        limit = 20;
        render();
      });
    });

    $("q").addEventListener("input", () => {
      limit = 20;
      render();
    });

    $("more").addEventListener("click", () => {
      limit += 20;
      render();
    });

    $("status").textContent = "Новости загружены. Обновлено: " +
      new Date(data.updated).toLocaleString("ru-RU");

    $("health").replaceChildren();

    (data.sources || []).forEach(source => {
      const li = document.createElement("li");
      li.textContent =
        (source.ok ? "✅ " : "❌ ") +
        source.source + ": " + source.count;
      $("health").appendChild(li);
    });

    render();

  } catch (error) {
    $("status").textContent =
      "Ошибка загрузки новостей: " + error.message;
  }
});