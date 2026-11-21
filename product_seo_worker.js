/**
 * N.O.D.E. Product SEO & Social Share Worker
 * Cloudflare Edge Worker for WhatsApp, Social Media Link Previews, and Google Search Indexing.
 *
 * Capabilities:
 * 1. Generates rich Open Graph (OG) tags for WhatsApp, Facebook, Twitter, Telegram, LinkedIn.
 * 2. Injects Schema.org JSON-LD (Product + Wholesale Offer) for Google Search Rich Results.
 * 3. Dynamically generates /sitemap.xml pulling published products from Supabase.
 * 4. Passes non-bot traffic directly to the N.O.D.E. Web Application (GitHub Pages).
 *
 * Version: 1.0.0
 */

const WEB_APP_HOST = "node.crimchart.com";
const FALLBACK_IMAGE = "https://node.crimchart.com/favicon.ico";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Dynamic Google XML Sitemap
    if (url.pathname === "/sitemap.xml") {
      return generateProductSitemap(request, env, url);
    }

    // 2. Robots.txt
    if (url.pathname === "/robots.txt") {
      const robots = `User-agent: *\nAllow: /\nSitemap: https://${WEB_APP_HOST}/sitemap.xml\n`;
      return new Response(robots, {
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
      });
    }

    // 3. Match Product URL Patterns:
    // /product/:id, /product/specs/:id, /product/order/:id
    const pathParts = url.pathname.split("/").filter(Boolean);
    let productId = null;

    if (pathParts[0] === "product" && pathParts.length >= 2) {
      if (pathParts[1] === "specs" || pathParts[1] === "order") {
        productId = pathParts[2];
      } else {
        productId = pathParts[1];
      }
    }

    // If not a product route or missing ID, forward to GitHub Pages
    if (!productId) {
      return fetchFromWebApp(request, env, url);
    }

    // 4. Detect Search Engine Bots & Social Media Scrapers
    const userAgent = (request.headers.get("User-Agent") || "").toLowerCase();
    const isBot = /whatsapp|facebookexternalhit|twitterbot|linkedinbot|slackbot|discordbot|telegrambot|applebot|googlebot|bingbot|yandex|duckduckbot|skypeuripreview|pinterest/.test(userAgent);

    if (isBot) {
      const product = await fetchProductFromSupabase(productId, env);
      if (product) {
        return generateProductHtmlResponse(product, url);
      }
    }

    // For regular users in browsers, proxy the SPA
    return fetchFromWebApp(request, env, url);
  },
};

/**
 * Proxy regular browser visits to the web application
 */
async function fetchFromWebApp(request, env, url) {
  let response = await fetch(request);
  if (response.status === 404) {
    const rootUrl = new URL(url.toString());
    rootUrl.pathname = "/";
    response = await fetch(rootUrl.toString(), {
      method: request.method,
      headers: request.headers,
    });
  }
  return new Response(response.body, {
    status: response.status === 404 ? 200 : response.status,
    statusText: response.status === 404 ? "OK" : response.statusText,
    headers: response.headers,
  });
}

/**
 * Query Supabase REST API for the product metadata
 */
async function fetchProductFromSupabase(productId, env) {
  const supabaseUrl = (env.SUPABASE_URL || "").replace(/\/+$/, "");
  const anonKey = env.SUPABASE_ANON_KEY || "";

  if (!supabaseUrl || !anonKey) {
    return null;
  }

  const headers = {
    apikey: anonKey,
    Authorization: `Bearer ${anonKey}`,
  };

  try {
    const endpoint = `${supabaseUrl}/rest/v1/products?id=eq.${encodeURIComponent(productId)}&select=*`;
    const res = await fetch(endpoint, { headers });
    if (!res.ok) return null;
    const data = await res.json();
    if (Array.isArray(data) && data.length > 0) {
      return data[0];
    }
  } catch (err) {
    console.error("Error fetching product from Supabase:", err);
  }
  return null;
}

/**
 * Build rich HTML page with Open Graph and Schema.org JSON-LD
 */
function generateProductHtmlResponse(product, url) {
  const name = product.name || "Wholesale Product";
  const brand = product.brand ? `${product.brand} ` : "";
  const srp = Number(product.srp) || 0;
  const priceFormatted = srp > 0 ? `UGX ${srp.toLocaleString("en-US")}` : "";
  const priceDisplay = priceFormatted ? `Wholesale ${priceFormatted}` : "Wholesale Wholesale Direct";

  // Title: "2 stand rail net — Wholesale UGX 120,000 | N.O.D.E."
  const metaTitle = `${name} — ${priceDisplay} | N.O.D.E.`;

  // Image: main high-resolution image
  const imageUrl = product.image_url || (Array.isArray(product.media_urls) && product.media_urls[0]) || FALLBACK_IMAGE;

  // Description formatted for WhatsApp preview
  const warehouse = product.warehouse_loc || "Kampala, Uganda";
  const moqText = product.moq ? ` • Min. Order: ${product.moq} Units` : "";
  const rawDesc = product.seo_description || product.description || `Wholesale ${name} available directly from verified suppliers in ${warehouse}.`;
  const cleanDesc = rawDesc.replace(/\s+/g, " ").trim().slice(0, 200);

  const metaDescription = `${priceFormatted ? `${priceFormatted} / unit${moqText} • ` : ""}Supplier: ${brand || "Verified"}(${warehouse}). ${cleanDesc}`;

  // JSON-LD Structured Data for Google Search
  const jsonLd = {
    "@context": "https://schema.org/",
    "@type": "Product",
    name: name,
    image: [imageUrl],
    description: cleanDesc,
    sku: product.sku || `ND-${product.id.slice(0, 8).toUpperCase()}`,
    brand: {
      "@type": "Brand",
      name: product.brand || "N.O.D.E. Verified",
    },
    offers: {
      "@type": "Offer",
      url: url.toString(),
      priceCurrency: "UGX",
      price: srp > 0 ? String(srp) : "0",
      itemCondition: "https://schema.org/NewCondition",
      availability: (product.current_stock ?? 1) > 0
        ? "https://schema.org/InStock"
        : "https://schema.org/PreOrder",
      seller: {
        "@type": "Organization",
        name: product.supplier_name || product.brand || "N.O.D.E. Wholesale Supplier",
      },
    },
  };

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(metaTitle)}</title>
  
  <!-- Primary Meta Tags -->
  <meta name="title" content="${escapeHtml(metaTitle)}">
  <meta name="description" content="${escapeHtml(metaDescription)}">
  <link rel="canonical" href="${escapeHtml(url.toString())}">

  <!-- Open Graph / Facebook / WhatsApp -->
  <meta property="og:type" content="product">
  <meta property="og:site_name" content="N.O.D.E. Wholesale Network">
  <meta property="og:url" content="${escapeHtml(url.toString())}">
  <meta property="og:title" content="${escapeHtml(metaTitle)}">
  <meta property="og:description" content="${escapeHtml(metaDescription)}">
  <meta property="og:image" content="${escapeHtml(imageUrl)}">
  <meta property="og:image:secure_url" content="${escapeHtml(imageUrl)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="${escapeHtml(name)}">
  ${srp > 0 ? `<meta property="product:price:amount" content="${srp}">
  <meta property="product:price:currency" content="UGX">` : ""}

  <!-- Twitter -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:url" content="${escapeHtml(url.toString())}">
  <meta name="twitter:title" content="${escapeHtml(metaTitle)}">
  <meta name="twitter:description" content="${escapeHtml(metaDescription)}">
  <meta name="twitter:image" content="${escapeHtml(imageUrl)}">

  <!-- Schema.org JSON-LD for Google Search -->
  <script type="application/ld+json">
${JSON.stringify(jsonLd, null, 2)}
  </script>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0D1117; color: #E6EDF3; padding: 24px;">
  <div style="max-width: 600px; margin: 40px auto; text-align: center;">
    <img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(name)}" style="width: 100%; max-height: 400px; object-fit: cover; border-radius: 12px; margin-bottom: 20px;">
    <h1 style="font-size: 24px; margin-bottom: 8px;">${escapeHtml(name)}</h1>
    <p style="font-size: 18px; color: #12BBF0; font-weight: bold; margin-bottom: 12px;">${escapeHtml(priceFormatted)} / unit</p>
    <p style="font-size: 14px; color: #8B949E; line-height: 1.5;">${escapeHtml(cleanDesc)}</p>
    <a href="${escapeHtml(url.pathname)}" style="display: inline-block; background: #12BBF0; color: #FFF; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; margin-top: 20px;">
      Open in N.O.D.E. App
    </a>
  </div>
  <script>
    // Seamless browser redirect for human visitors who open this directly
    if (typeof window !== 'undefined' && !/bot|crawl|slurp|spider/i.test(navigator.userAgent)) {
      window.location.replace("${url.pathname}");
    }
  </script>
</body>
</html>`;

  return new Response(html, {
    headers: {
      "Content-Type": "text/html;charset=UTF-8",
      "Cache-Control": "public, max-age=3600, s-maxage=86400",
    },
  });
}

/**
 * Generate dynamic XML sitemap containing all published products
 */
async function generateProductSitemap(request, env, url) {
  const supabaseUrl = (env.SUPABASE_URL || "").replace(/\/+$/, "");
  const anonKey = env.SUPABASE_ANON_KEY || "";

  if (!supabaseUrl || !anonKey) {
    return new Response("Database not configured", { status: 500 });
  }

  const headers = {
    apikey: anonKey,
    Authorization: `Bearer ${anonKey}`,
  };

  let xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://${WEB_APP_HOST}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>`;

  try {
    const endpoint = `${supabaseUrl}/rest/v1/products?select=id,updated_at,created_at&order=created_at.desc&limit=2000`;
    const res = await fetch(endpoint, { headers });

    if (res.ok) {
      const products = await res.json();
      for (const p of products) {
        const lastMod = (p.updated_at || p.created_at || new Date().toISOString()).split("T")[0];
        xml += `
  <url>
    <loc>https://${WEB_APP_HOST}/product/${p.id}</loc>
    <lastmod>${lastMod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>`;
      }
    }
  } catch (err) {
    console.error("Error generating sitemap:", err);
  }

  xml += `
</urlset>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml;charset=UTF-8",
      "Cache-Control": "public, max-age=86400, s-maxage=86400",
    },
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
