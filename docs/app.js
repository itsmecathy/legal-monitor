
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

  const platformNames = {
    telegram: "Telegram",
    max: "MAX",
    vk: "ВКонтакте",
    web: "Сайты и RSS"
  };

  const values = (item, key) =>
    [item[key] || []].flat();

  function getPlatform(item) {
    try {
      const host = new URL(item.url).hostname
        .toLowerCase()
        .replace(/^www\./, "");

      if (["t.me", "telegram.me", "telegram.org"].includes(host)) {
        return "telegram";
      }

      if (host === "max.ru" || host.endsWith(".max.ru")) {
        return "max";
      }

      if (
        host === "vk.com" ||
        host.endsWith(".vk.com") ||
        host === "vk.ru" ||
        host.endsWith(".vk.ru") ||
        host === "vkontakte.ru"
      ) {
        return "vk";
      }
    } catch (error) {}

    return "web";
  }

  function render() {
    const q = ($("q")?.value || "")
      .toLowerCase().trim();

    const from = $("dateFrom")?.value || "";
    const to = $("dateTo")?.value || "";
    const platform = $("platform")?.value || "";

    const filtered = items.filter(item => {
      if (platform && getPlatform(item) !== platform) {
        return false;
      }

      const published = new Date(item.published);

      if (from || to) {
        if (
          !item.published ||
          isNaN(published.getTime())
        ) {
          return false;
        }

        if (
          from &&
          published < new Date(from + "T00:00:00")
        ) {
          return false;
        }

        if (
          to &&
          published > new Date(to + "T23:59:59.999")
        ) {
          return false;
        }
      }

      const text = JSON.stringify(item).toLowerCase();

      return text.includes(q) &&
        Object.entries(filters).every(([id, key]) =>
          !$(id)?.value ||
          values(item, key).includes($(id).value)
        );
    });

    filtered.sort((a, b) =>
      (Date.parse(b.published) || 0) -
      (Date.parse(a.published) || 0)
    );

    $("items").replaceChildren();

    $("count").textContent =
      `Найдено: ${filtered.length}`;

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
        platformNames[getPlatform(item)],
        item.source,
        item.published
          ? new Date(item.published)
              .toLocaleDateString("ru-RU")
          : "",
        ...values(item, "laws"),
        item.news_type
      ].filter(Boolean).join(" • ");

      card.append(title, info);
      $("items").appendChild(card);
    });

    if (filtered.length === 0) {
      const message = document.createElement("p");

      message.textContent =
        "По выбранным фильтрам новости не найдены.";

      $("items").appendChild(message);
    }

    if ($("more")) {
      $("more").hidden = filtered.length <= limit;
    }
  }

  function refresh() {
    limit = 20;
    render();
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

    items = Array.isArray(data.items)
      ? data.items
      : [];

    Object.entries(filters).forEach(([id, key]) => {
      const select = $(id);

      if (!select) return;

      const unique = [...new Set(
        items.flatMap(item => values(item, key))
      )].sort();

      unique.forEach(value => {
        if (
          ![...select.options].some(
            o => o.value === value
          )
        ) {
          select.add(new Option(value, value));
        }
      });

      select.addEventListener("change", refresh);
    });

    if ($("platform")) {
      for (const option of $("platform").options) {
        const count = option.value
          ? items.filter(
              item => getPlatform(item) === option.value
            ).length
          : items.length;

        const name = option.value
          ? platformNames[option.value]
          : "Все платформы";

        option.textContent = `${name} (${count})`;
      }

      $("platform").addEventListener(
        "change", refresh
      );
    }

    $("q")?.addEventListener("input", refresh);

    $("dateFrom")?.addEventListener(
      "change", refresh
    );

    $("dateTo")?.addEventListener(
      "change", refresh
    );

    $("resetDates")?.addEventListener("click", () => {
      if ($("dateFrom")) $("dateFrom").value = "";
      if ($("dateTo")) $("dateTo").value = "";
      refresh();
    });

    $("more")?.addEventListener("click", () => {
      limit += 20;
      render();
    });

    if ($("status")) {
      $("status").textContent =
        "Новости загружены. Всего в базе: " +
        items.length +
        (data.updated
          ? ". Обновлено: " +
            new Date(data.updated)
              .toLocaleString("ru-RU")
          : "");
    }

    if ($("health")) {
      $("health").replaceChildren();

      (data.sources || []).forEach(source => {
        const li = document.createElement("li");

        li.textContent =
          (source.ok ? "✅ " : "❌ ") +
          source.source + ": " + source.count;

        $("health").appendChild(li);
      });
    }

    render();

  } catch (error) {
    console.error(error);

    if ($("status")) {
      $("status").textContent =
        "Ошибка загрузки новостей: " + error.message;
    }
  }
});
