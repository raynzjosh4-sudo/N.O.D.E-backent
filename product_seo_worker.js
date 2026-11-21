/**
 * N.O.D.E. Product SEO & Social Share Worker
 * Cloudflare Edge Worker for WhatsApp, Social Media Link Previews, and Google Search Indexing.
 *
 * Capabilities:
 * 1. Serves official app icon (nodeicon_full.png, favicon.png, favicon.ico) directly from edge memory.
 * 2. Generates rich Open Graph (OG) tags for WhatsApp, Facebook, Twitter, Telegram, LinkedIn.
 * 3. Injects Schema.org JSON-LD (Product, SoftwareApplication, Organization, WebSite) for Google Search Rich Results.
 * 4. Dynamically generates /sitemap.xml with Google Image Sitemap extensions (<image:image>) for app and products.
 * 5. Serves /robots.txt pointing to the sitemap.
 * 6. Provides JSON API endpoints (/api/share/product/:id and /api/share/app) for multi-platform sharing.
 * 7. Delivers rich SEO landing HTML for search engine crawlers and bots on root, terms, and product pages.
 * 8. Passes real browser visitors seamlessly to the N.O.D.E. Web Application (GitHub Pages).
 *
 * Version: 2.1.0
 */

import { APP_ICON_BASE64 } from "./app_icon.js";

const WEB_APP_HOST = "node.crimchart.com";
const APP_ICON_URL = `https://${WEB_APP_HOST}/nodeicon_full.png`;
const FALLBACK_IMAGE = APP_ICON_URL;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Direct High-Speed Edge Icon Serving (Instant response worldwide)
    if (
      url.pathname === "/nodeicon_full.png" ||
      url.pathname === "/favicon.png" ||
      url.pathname === "/favicon.ico" ||
      url.pathname === "/apple-touch-icon.png" ||
      url.pathname === "/assets/images/icon.png" ||
      url.pathname === "/icon.png"
    ) {
      return serveAppIcon();
    }

    // 2. Dynamic Google XML Sitemap (Includes Google Image Extensions)
    if (url.pathname === "/sitemap.xml") {
      return generateFullSitemap(request, env, url);
    }

    // 3. Robots.txt
    if (url.pathname === "/robots.txt") {
      const robots = `User-agent: *\nAllow: /\nSitemap: https://${WEB_APP_HOST}/sitemap.xml\n`;
      return new Response(robots, {
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
      });
    }

    // 3b. Google Search Console HTML File Verification
    if (/^\/google[a-z0-9]+\.html$/i.test(url.pathname)) {
      const token = url.pathname.replace(/^\/google/, "").replace(/\.html$/, "");
      return new Response(`google-site-verification: google${token}.html`, {
        headers: { "Content-Type": "text/html;charset=UTF-8" },
      });
    }

    // 4. App Info API Endpoint (/api/share/app)
    if (url.pathname === "/api/share/app") {
      return Response.json(
        {
          name: "N.O.D.E.",
          fullName: "National Online Distribution & Enterprise",
          tagline: "Uganda's B2B Wholesale Marketplace & Verified Supplier Network",
          url: `https://${WEB_APP_HOST}`,
          icon: APP_ICON_URL,
          termsUrl: `https://${WEB_APP_HOST}/terms`,
          privacyUrl: `https://${WEB_APP_HOST}/privacy`,
          description:
            "N.O.D.E. (National Online Distribution & Enterprise) connects retailers and businesses across Uganda directly with verified wholesale suppliers, manufacturers, and importers in Kampala. Bulk tier pricing, escrow guarantee, and minimum order quantities.",
        },
        { headers: corsHeaders }
      );
    }

    // 5. Match Product URL Patterns:
    // /product/:id, /product/specs/:id, /product/order/:id, /api/share/product/:id
    const pathParts = url.pathname.split("/").filter(Boolean);
    let productId = null;
    let isApiShareRequest = false;

    if (pathParts[0] === "api" && pathParts[1] === "share" && pathParts[2] === "product" && pathParts[3]) {
      productId = pathParts[3];
      isApiShareRequest = true;
    } else if (pathParts[0] === "product" && pathParts.length >= 2) {
      if (pathParts[1] === "specs" || pathParts[1] === "order") {
        productId = pathParts[2];
      } else {
        productId = pathParts[1];
      }
    }

    // If calling Product API:
    if (isApiShareRequest || (productId && url.searchParams.get("format") === "json")) {
      const product = await fetchProductFromSupabase(productId, env);
      if (!product) {
        return Response.json({ error: "Product not found" }, { status: 404, headers: corsHeaders });
      }
      return generateProductJsonResponse(product, url);
    }

    // 6. Detect Search Engine Bots, Google Inspection Tools & Social Media Scrapers
    const userAgent = (request.headers.get("User-Agent") || "").toLowerCase();
    const isBot = /google|inspection|whatsapp|facebook|twitter|linkedin|slack|discord|telegram|apple|bing|yandex|duckduck|pinterest|crawler|spider|bot|lighthouse/i.test(
      userAgent
    );

    // If requesting a product:
    if (productId) {
      const product = await fetchProductFromSupabase(productId, env);
      if (product) {
        if (isBot) {
          return generateProductHtmlResponse(product, url);
        }
        // For browsers and headless testing tools, inject structured data directly into the SPA shell
        return fetchFromWebAppWithMetadata(request, env, url, product);
      }
    }

    // If bot requesting Trading Terms (/terms):
    if (isBot && url.pathname === "/terms") {
      return generateTermsHtmlResponse(url);
    }

    // If bot requesting Homepage or root (/):
    if (isBot && (url.pathname === "/" || url.pathname === "")) {
      return generateHomepageHtmlResponse(env, url);
    }

    // 7. For regular users in web browsers, proxy to the React Native Web SPA
    return fetchFromWebApp(request, env, url);
  },
};

/**
 * Serve official binary app icon from edge memory
 */
function serveAppIcon() {
  const binaryString = atob(APP_ICON_BASE64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  return new Response(bytes.buffer, {
    headers: {
      "Content-Type": "image/png",
      "Cache-Control": "public, max-age=31536000, immutable",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

/**
 * Proxy regular browser visits to the web application (GitHub Pages)
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
 * Proxy browser visits while injecting Schema.org JSON-LD and meta tags into SPA shell
 */
async function fetchFromWebAppWithMetadata(request, env, url, product) {
  let response = await fetch(request);
  if (response.status === 404) {
    const rootUrl = new URL(url.toString());
    rootUrl.pathname = "/";
    response = await fetch(rootUrl.toString(), {
      method: request.method,
      headers: request.headers,
    });
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("text/html")) {
    return response;
  }

  let html = await response.text();
  const pricing = getWholesalePricing(product);
  const name = product.name || "Wholesale Product";
  const brand = product.brand || "N.O.D.E. Verified";
  const imageUrl = product.image_url || (Array.isArray(product.media_urls) && product.media_urls[0]) || APP_ICON_URL;

  const jsonLd = {
    "@context": "https://schema.org/",
    "@type": "Product",
    name: name,
    image: [imageUrl],
    description: product.description || `Wholesale ${name} on N.O.D.E.`,
    sku: product.sku || `ND-${product.id.slice(0, 8).toUpperCase()}`,
    brand: {
      "@type": "Brand",
      name: brand,
    },
    offers: {
      "@type": "Offer",
      url: url.toString(),
      priceCurrency: "UGX",
      price: pricing.price > 0 ? String(pricing.price) : "0",
      priceValidUntil: "2027-12-31",
      itemCondition: "https://schema.org/NewCondition",
      availability: (product.current_stock ?? 1) > 0 ? "https://schema.org/InStock" : "https://schema.org/PreOrder",
      seller: {
        "@type": "Organization",
        name: product.supplier_name || brand || "N.O.D.E. Wholesale Supplier",
        logo: APP_ICON_URL,
      },
    },
  };

  const injection = `
  <title>${escapeHtml(name)} — ${escapeHtml(pricing.priceDisplay)} | N.O.D.E.</title>
  <meta name="description" content="${escapeHtml(name)} — ${escapeHtml(pricing.priceDisplay)} (MOQ: ${pricing.moq} pcs). ${escapeHtml(pricing.tierSummary)}">
  <meta property="og:title" content="${escapeHtml(name)} — ${escapeHtml(pricing.priceDisplay)} | N.O.D.E.">
  <meta property="og:image" content="${escapeHtml(imageUrl)}">
  <script type="application/ld+json">
${JSON.stringify(jsonLd, null, 2)}
  </script>
</head>`;

  html = html.replace("</head>", `${injection}`);

  return new Response(html, {
    status: 200,
    headers: {
      "Content-Type": "text/html;charset=UTF-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}

/**
 * Extract wholesale tier pricing (volume tiers take precedence over retail SRP)
 */
function getWholesalePricing(product) {
  let tiers = [];
  try {
    if (Array.isArray(product.price_tiers)) {
      tiers = product.price_tiers;
    } else if (typeof product.price_tiers === "string") {
      tiers = JSON.parse(product.price_tiers);
    }
  } catch (e) {}

  if (tiers && tiers.length > 0) {
    const validTiers = tiers
      .filter((t) => t && Number(t.price) > 0)
      .sort((a, b) => Number(a.minQuantity || 1) - Number(b.minQuantity || 1));

    if (validTiers.length > 0) {
      const prices = validTiers.map((t) => Number(t.price));
      const minWholesalePrice = Math.min(...prices);
      const maxWholesalePrice = Math.max(...prices);
      const startingTier = validTiers[0];
      const moq = startingTier.minQuantity || product.moq || 1;

      const tierSummary = validTiers
        .map((t) => {
          const qty = t.minQuantity ? `${t.minQuantity}+ pcs` : (t.label || "Tier");
          return `${qty}: UGX ${Number(t.price).toLocaleString("en-US")}`;
        })
        .join(" • ");

      const priceDisplay =
        minWholesalePrice === maxWholesalePrice
          ? `Wholesale UGX ${minWholesalePrice.toLocaleString("en-US")} / unit`
          : `Wholesale from UGX ${minWholesalePrice.toLocaleString("en-US")} / unit`;

      return {
        hasTiers: true,
        price: minWholesalePrice,
        priceFormatted: `UGX ${minWholesalePrice.toLocaleString("en-US")}`,
        minWholesalePrice,
        maxWholesalePrice,
        moq,
        priceDisplay,
        tierSummary,
        validTiers,
      };
    }
  }

  const srp = Number(product.srp) || 0;
  return {
    hasTiers: false,
    price: srp,
    priceFormatted: srp > 0 ? `UGX ${srp.toLocaleString("en-US")}` : "",
    minWholesalePrice: srp,
    maxWholesalePrice: srp,
    moq: product.moq || 1,
    priceDisplay: srp > 0 ? `UGX ${srp.toLocaleString("en-US")} / unit` : "Wholesale Direct",
    tierSummary: "",
    validTiers: [],
  };
}

/**
 * Query Supabase REST API for product metadata
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
    const endpoint = `${supabaseUrl}/rest/v1/products_table?id=eq.${encodeURIComponent(productId)}&select=*`;
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
 * Return JSON share metadata for apps
 */
function generateProductJsonResponse(product, url) {
  const pricing = getWholesalePricing(product);
  const name = product.name || "Wholesale Product";
  const brand = product.brand || "";
  const imageUrl = product.image_url || (Array.isArray(product.media_urls) && product.media_urls[0]) || FALLBACK_IMAGE;
  const warehouse = product.warehouse_loc || "Kampala, Uganda";
  const shareUrl = `https://${WEB_APP_HOST}/product/specs/${product.id}`;
  const shareText = `*${name}* — ${pricing.priceDisplay}\nWholesale Tiers: ${pricing.tierSummary || pricing.priceFormatted}\nOrder on N.O.D.E. Wholesale:\n${shareUrl}`;

  return Response.json(
    {
      id: product.id,
      name,
      brand,
      price: pricing.price,
      retailPrice: Number(product.srp) || 0,
      currency: "UGX",
      priceFormatted: pricing.priceFormatted,
      priceDisplay: pricing.priceDisplay,
      priceTiers: pricing.validTiers,
      tierSummary: pricing.tierSummary,
      imageUrl,
      warehouse,
      moq: pricing.moq,
      shareUrl,
      whatsappUrl: `https://wa.me/?text=${encodeURIComponent(shareText)}`,
      shareText,
      appIcon: APP_ICON_URL,
    },
    { headers: corsHeaders }
  );
}

/**
 * Build rich HTML page with Open Graph and Schema.org JSON-LD for Products
 */
function generateProductHtmlResponse(product, url) {
  const pricing = getWholesalePricing(product);
  const name = product.name || "Wholesale Product";
  const brand = product.brand ? `${product.brand} ` : "";
  const imageUrl = product.image_url || (Array.isArray(product.media_urls) && product.media_urls[0]) || FALLBACK_IMAGE;
  const warehouse = product.warehouse_loc || "Kampala, Uganda";
  const moqText = ` • Min. Order: ${pricing.moq} Units`;
  const rawDesc =
    product.seo_description ||
    product.description ||
    `Wholesale ${name} available directly from verified suppliers in ${warehouse} on N.O.D.E. (National Online Distribution & Enterprise).`;
  const cleanDesc = rawDesc.replace(/\s+/g, " ").trim().slice(0, 200);

  const metaTitle = `${name} — ${pricing.priceDisplay} | N.O.D.E.`;
  const metaDescription = `${pricing.priceFormatted ? `${pricing.priceFormatted} / unit${moqText} • ` : ""}${pricing.tierSummary ? `Tiers: ${pricing.tierSummary} • ` : ""}Supplier: ${
    brand || "Verified"
  }(${warehouse}). ${cleanDesc}`;

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
      price: pricing.price > 0 ? String(pricing.price) : "0",
      priceValidUntil: "2027-12-31",
      itemCondition: "https://schema.org/NewCondition",
      availability: (product.current_stock ?? 1) > 0 ? "https://schema.org/InStock" : "https://schema.org/PreOrder",
      seller: {
        "@type": "Organization",
        name: product.supplier_name || product.brand || "N.O.D.E. Wholesale Supplier",
        logo: APP_ICON_URL,
      },
    },
  };

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(metaTitle)}</title>
  
  <meta name="title" content="${escapeHtml(metaTitle)}">
  <meta name="description" content="${escapeHtml(metaDescription)}">
  <meta name="google-site-verification" content="uteCDPNtn4l-xAMk1xTZ1qOP8zDoABq96chhXfdMqKc">
  <link rel="canonical" href="${escapeHtml(url.toString())}">
  <link rel="icon" type="image/png" href="${APP_ICON_URL}">
  <link rel="apple-touch-icon" href="${APP_ICON_URL}">

  <!-- Open Graph / WhatsApp / Facebook -->
  <meta property="og:type" content="product">
  <meta property="og:site_name" content="N.O.D.E. Wholesale — National Online Distribution & Enterprise">
  <meta property="og:url" content="${escapeHtml(url.toString())}">
  <meta property="og:title" content="${escapeHtml(metaTitle)}">
  <meta property="og:description" content="${escapeHtml(metaDescription)}">
  <meta property="og:image" content="${escapeHtml(imageUrl)}">
  <meta property="og:image:secure_url" content="${escapeHtml(imageUrl)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="${escapeHtml(name)}">
  ${pricing.price > 0 ? `<meta property="product:price:amount" content="${pricing.price}">\n  <meta property="product:price:currency" content="UGX">` : ""}

  <!-- Twitter -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:url" content="${escapeHtml(url.toString())}">
  <meta name="twitter:title" content="${escapeHtml(metaTitle)}">
  <meta name="twitter:description" content="${escapeHtml(metaDescription)}">
  <meta name="twitter:image" content="${escapeHtml(imageUrl)}">

  <!-- Schema.org JSON-LD -->
  <script type="application/ld+json">
${JSON.stringify(jsonLd, null, 2)}
  </script>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0D1117; color: #E6EDF3; padding: 24px;">
  <div style="max-width: 600px; margin: 40px auto; text-align: center;">
    <div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 24px;">
      <img src="${APP_ICON_URL}" alt="N.O.D.E. Logo" style="width: 44px; height: 44px; border-radius: 8px;">
      <span style="font-size: 20px; font-weight: bold; letter-spacing: 1px;">N.O.D.E. Wholesale</span>
    </div>
    <img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(name)}" style="width: 100%; max-height: 400px; object-fit: cover; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.5);">
    <h1 style="font-size: 24px; margin-bottom: 8px;">${escapeHtml(name)}</h1>
    <p style="font-size: 20px; color: #12BBF0; font-weight: bold; margin-bottom: 6px;">${escapeHtml(pricing.priceDisplay)}</p>
    ${pricing.tierSummary ? `<p style="font-size: 13px; color: #58A6FF; margin-bottom: 12px; font-weight: 500;">${escapeHtml(pricing.tierSummary)}</p>` : ""}
    <p style="font-size: 14px; color: #8B949E; line-height: 1.6; margin-bottom: 24px;">${escapeHtml(cleanDesc)}</p>
    <a href="${escapeHtml(url.pathname)}" style="display: inline-block; background: #12BBF0; color: #FFF; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 16px;">
      Open in N.O.D.E. App
    </a>
  </div>
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
 * Rich SEO Landing Page for Google Search bot crawling root (/)
 */
async function generateHomepageHtmlResponse(env, url) {
  const metaTitle = "N.O.D.E. Wholesale — National Online Distribution & Enterprise | Uganda B2B Marketplace";
  const metaDesc =
    "N.O.D.E. (National Online Distribution & Enterprise) is Uganda's premier B2B wholesale marketplace connecting retailers and businesses directly with verified manufacturers, importers, and wholesale suppliers in Kampala. Access bulk tier pricing, verified stock, and escrow-backed ordering.";

  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `https://${WEB_APP_HOST}/#website`,
        url: `https://${WEB_APP_HOST}`,
        name: "N.O.D.E. Wholesale",
        alternateName: ["N.O.D.E.", "NODE Wholesale", "National Online Distribution & Enterprise"],
        description: metaDesc,
        potentialAction: {
          "@type": "SearchAction",
          target: `https://${WEB_APP_HOST}/search?q={search_term_string}`,
          "query-input": "required name=search_term_string",
        },
      },
      {
        "@type": "Organization",
        "@id": `https://${WEB_APP_HOST}/#organization`,
        name: "N.O.D.E. Wholesale Network",
        alternateName: "National Online Distribution & Enterprise",
        url: `https://${WEB_APP_HOST}`,
        logo: APP_ICON_URL,
        image: APP_ICON_URL,
        description: "B2B wholesale distribution platform linking retail businesses with manufacturers and wholesale distributors across Uganda.",
        address: {
          "@type": "PostalAddress",
          addressLocality: "Kampala",
          addressCountry: "Uganda",
        },
      },
      {
        "@type": "SoftwareApplication",
        name: "N.O.D.E. Wholesale App",
        operatingSystem: "Web, Android, iOS",
        applicationCategory: "BusinessApplication",
        applicationSubCategory: "ShoppingApplication",
        image: APP_ICON_URL,
        screenshot: APP_ICON_URL,
        description: "Official wholesale marketplace application for National Online Distribution & Enterprise (N.O.D.E.).",
        offers: {
          "@type": "Offer",
          price: "0",
          priceCurrency: "UGX",
        },
      },
    ],
  };

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(metaTitle)}</title>
  
  <meta name="title" content="${escapeHtml(metaTitle)}">
  <meta name="description" content="${escapeHtml(metaDesc)}">
  <meta name="google-site-verification" content="uteCDPNtn4l-xAMk1xTZ1qOP8zDoABq96chhXfdMqKc">
  <meta name="keywords" content="NODE, N.O.D.E., NODE Wholesale, National Online Distribution and Enterprise, wholesale Uganda, Kampala wholesale, bulk buying, B2B wholesale marketplace, verified suppliers, wholesale prices, trading terms">
  <link rel="canonical" href="https://${WEB_APP_HOST}/">
  <link rel="icon" type="image/png" href="${APP_ICON_URL}">
  <link rel="apple-touch-icon" href="${APP_ICON_URL}">

  <!-- Open Graph -->
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="N.O.D.E. Wholesale — National Online Distribution & Enterprise">
  <meta property="og:url" content="https://${WEB_APP_HOST}/">
  <meta property="og:title" content="${escapeHtml(metaTitle)}">
  <meta property="og:description" content="${escapeHtml(metaDesc)}">
  <meta property="og:image" content="${APP_ICON_URL}">
  <meta property="og:image:width" content="512">
  <meta property="og:image:height" content="512">
  <meta property="og:image:alt" content="N.O.D.E. App Icon">

  <!-- Twitter -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="${escapeHtml(metaTitle)}">
  <meta name="twitter:description" content="${escapeHtml(metaDesc)}">
  <meta name="twitter:image" content="${APP_ICON_URL}">

  <!-- Schema.org Structured Data -->
  <script type="application/ld+json">
${JSON.stringify(jsonLd, null, 2)}
  </script>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0D1117; color: #E6EDF3; padding: 32px 16px;">
  <main style="max-width: 800px; margin: 0 auto; text-align: center;">
    <img src="${APP_ICON_URL}" alt="N.O.D.E. App Icon" style="width: 96px; height: 96px; border-radius: 20px; box-shadow: 0 8px 32px rgba(18, 187, 240, 0.3); margin-bottom: 20px;">
    <h1 style="font-size: 32px; font-weight: 800; margin-bottom: 8px; color: #FFFFFF;">N.O.D.E. Wholesale</h1>
    <h2 style="font-size: 18px; font-weight: 500; color: #12BBF0; margin-bottom: 20px;">National Online Distribution &amp; Enterprise</h2>
    <p style="font-size: 16px; line-height: 1.6; color: #8B949E; margin-bottom: 32px;">
      Connecting retailers, merchants, and institutions directly with verified manufacturers, importers, and authorized wholesale suppliers in Kampala and across East Africa.
    </p>

    <!-- Quick Navigation Sitelinks for Googlebot -->
    <div style="display: flex; flex-wrap: wrap; justify-content: center; gap: 12px; margin-bottom: 40px;">
      <a href="/explore" style="background: #161B22; border: 1px solid #30363D; color: #58A6FF; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 600;">Explore Wholesale Products</a>
      <a href="/suppliers" style="background: #161B22; border: 1px solid #30363D; color: #58A6FF; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 600;">Verified Suppliers Directory</a>
      <a href="/search" style="background: #161B22; border: 1px solid #30363D; color: #58A6FF; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 600;">Search Wholesale Prices</a>
      <a href="/terms" style="background: #161B22; border: 1px solid #30363D; color: #58A6FF; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 600;">Wholesale Trading Terms</a>
      <a href="/privacy" style="background: #161B22; border: 1px solid #30363D; color: #58A6FF; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 600;">Privacy &amp; Escrow Guarantee</a>
    </div>

    <footer style="margin-top: 60px; font-size: 13px; color: #6E7681; border-top: 1px solid #21262D; padding-top: 24px;">
      <p>&copy; ${new Date().getFullYear()} N.O.D.E. Wholesale (National Online Distribution &amp; Enterprise). All rights reserved.</p>
      <p>Stanbic Escrow Protection &bull; EFRIS Tax Invoice Invoicing &bull; UNBS Quality Compliance</p>
    </footer>
  </main>
  <script>
    if (typeof window !== 'undefined' && !/bot|crawl|slurp|spider/i.test(navigator.userAgent)) {
      window.location.replace("/");
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
 * Rich SEO Landing Page for Trading Terms (/terms)
 */
function generateTermsHtmlResponse(url) {
  const metaTitle = "Wholesale Trading Terms & Policies | N.O.D.E. (National Online Distribution & Enterprise)";
  const metaDesc =
    "Official Wholesale Trading Terms governing bulk purchase commitments, minimum order quantities (MOQs), Stanbic Bank escrow protection, 48-hour inspection window, and UNBS EFRIS compliance on N.O.D.E.";

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(metaTitle)}</title>
  <meta name="description" content="${escapeHtml(metaDesc)}">
  <link rel="canonical" href="https://${WEB_APP_HOST}/terms">
  <link rel="icon" type="image/png" href="${APP_ICON_URL}">
  <link rel="apple-touch-icon" href="${APP_ICON_URL}">
  <meta property="og:title" content="${escapeHtml(metaTitle)}">
  <meta property="og:description" content="${escapeHtml(metaDesc)}">
  <meta property="og:image" content="${APP_ICON_URL}">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0D1117; color: #E6EDF3; padding: 32px 16px;">
  <div style="max-width: 720px; margin: 0 auto;">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 24px;">
      <img src="${APP_ICON_URL}" alt="N.O.D.E. Icon" style="width: 48px; height: 48px; border-radius: 12px;">
      <div>
        <h1 style="font-size: 24px; margin: 0; color: #FFF;">Wholesale Trading Terms &amp; Policies</h1>
        <p style="font-size: 14px; margin: 4px 0 0; color: #12BBF0;">N.O.D.E. (National Online Distribution &amp; Enterprise)</p>
      </div>
    </div>
    
    <div style="background: #161B22; border: 1px solid #30363D; border-radius: 12px; padding: 24px; margin-bottom: 20px;">
      <h2 style="font-size: 18px; color: #58A6FF; margin-top: 0;">1. Order Commitments &amp; MOQs</h2>
      <p style="color: #C9D1D9; line-height: 1.6;">Wholesale tier prices are strictly binding upon confirmation of order quantities. Sub-tier adjustments are subject to supplier approval before freight dispatch.</p>

      <h2 style="font-size: 18px; color: #58A6FF; margin-top: 24px;">2. Escrow &amp; Payment Release</h2>
      <p style="color: #C9D1D9; line-height: 1.6;">Funds deposited via Stanbic Bank escrow or Mobile Money Business are safeguarded until signed delivery confirmation is uploaded by the warehouse manager.</p>

      <h2 style="font-size: 18px; color: #58A6FF; margin-top: 24px;">3. Inspection Window &amp; Returns</h2>
      <p style="color: #C9D1D9; line-height: 1.6;">Buyers have forty-eight (48) hours from arrival to conduct QA batch sampling. Manufacturer warranties apply for 14 to 90 days depending on verified catalog classification.</p>

      <h2 style="font-size: 18px; color: #58A6FF; margin-top: 24px;">4. UNBS &amp; East African Compliance</h2>
      <p style="color: #C9D1D9; line-height: 1.6;">All electronic, chemical, and consumable wholesale lots include digital UNBS QA certifications and tax invoice receipts compliant with EFRIS regulations.</p>
    </div>

    <a href="/" style="display: inline-block; color: #12BBF0; text-decoration: none; font-weight: 600;">&larr; Back to N.O.D.E. Homepage</a>
  </div>
</body>
</html>`;

  return new Response(html, {
    headers: {
      "Content-Type": "text/html;charset=UTF-8",
      "Cache-Control": "public, max-age=86400",
    },
  });
}

/**
 * Generate dynamic XML sitemap with Google Image Sitemap standard
 */
async function generateFullSitemap(request, env, url) {
  const supabaseUrl = (env.SUPABASE_URL || "").replace(/\/+$/, "");
  const anonKey = env.SUPABASE_ANON_KEY || "";

  let xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <!-- Homepage -->
  <url>
    <loc>https://${WEB_APP_HOST}/</loc>
    <image:image>
      <image:loc>${APP_ICON_URL}</image:loc>
      <image:title>N.O.D.E. Wholesale — National Online Distribution &amp; Enterprise</image:title>
      <image:caption>Uganda B2B Wholesale Marketplace &amp; Verified Supplier Network in Kampala</image:caption>
    </image:image>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <!-- Explore Wholesale Products -->
  <url>
    <loc>https://${WEB_APP_HOST}/explore</loc>
    <image:image>
      <image:loc>${APP_ICON_URL}</image:loc>
      <image:title>Explore Wholesale Categories and Direct Supplier Inventories</image:title>
    </image:image>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <!-- Search Wholesale Market -->
  <url>
    <loc>https://${WEB_APP_HOST}/search</loc>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <!-- Verified Suppliers Directory -->
  <url>
    <loc>https://${WEB_APP_HOST}/suppliers</loc>
    <image:image>
      <image:loc>${APP_ICON_URL}</image:loc>
      <image:title>Verified Wholesale Suppliers &amp; Importers Directory</image:title>
    </image:image>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
  <!-- Wholesale Trading Terms -->
  <url>
    <loc>https://${WEB_APP_HOST}/terms</loc>
    <image:image>
      <image:loc>${APP_ICON_URL}</image:loc>
      <image:title>N.O.D.E. Wholesale Trading Terms, Escrow &amp; Compliance</image:title>
    </image:image>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
  <!-- Privacy Policy -->
  <url>
    <loc>https://${WEB_APP_HOST}/privacy</loc>
    <image:image>
      <image:loc>${APP_ICON_URL}</image:loc>
      <image:title>N.O.D.E. Privacy Policy &amp; Escrow Guarantee</image:title>
    </image:image>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>`;

  if (supabaseUrl && anonKey) {
    const headers = {
      apikey: anonKey,
      Authorization: `Bearer ${anonKey}`,
    };

    try {
      // 1. Fetch all wholesale products
      const pRes = await fetch(`${supabaseUrl}/rest/v1/products_table?select=id,name,brand,srp,price_tiers,moq,image_url,media_urls,updated_at,created_at&order=created_at.desc&limit=2000`, { headers });
      if (pRes.ok) {
        const products = await pRes.json();
        for (const p of products) {
          const pricing = getWholesalePricing(p);
          const lastMod = (p.updated_at || p.created_at || new Date().toISOString()).split("T")[0];
          const img = p.image_url || (Array.isArray(p.media_urls) && p.media_urls[0]);
          const brandText = p.brand ? ` - ${escapeXml(p.brand)}` : "";
          const captionText = `${pricing.priceDisplay}${pricing.tierSummary ? ` • Tiers: ${pricing.tierSummary}` : ""}${brandText}`;

          // Specs URL
          xml += `
  <url>
    <loc>https://${WEB_APP_HOST}/product/specs/${p.id}</loc>
    <lastmod>${lastMod}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>${
      img
        ? `
    <image:image>
      <image:loc>${escapeXml(img)}</image:loc>
      <image:title>${escapeXml(p.name || "Wholesale Product")}</image:title>
      <image:caption>${escapeXml(captionText)}</image:caption>
    </image:image>`
        : ""
    }
  </url>`;

          // Canonical Product URL
          xml += `
  <url>
    <loc>https://${WEB_APP_HOST}/product/${p.id}</loc>
    <lastmod>${lastMod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>`;
        }
      }

      // 2. Fetch wholesale categories
      const cRes = await fetch(`${supabaseUrl}/rest/v1/categories_table?select=id,name,image_url,updated_at&limit=200`, { headers });
      if (cRes.ok) {
        const categories = await cRes.json();
        for (const c of categories) {
          const lastMod = (c.updated_at || new Date().toISOString()).split("T")[0];
          xml += `
  <url>
    <loc>https://${WEB_APP_HOST}/explore?category=${encodeURIComponent(c.id)}</loc>
    <lastmod>${lastMod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.85</priority>
    <image:image>
      <image:loc>${APP_ICON_URL}</image:loc>
      <image:title>${escapeXml(c.name || "Category")} Wholesale</image:title>
      <image:caption>Explore wholesale lots and verified suppliers for ${escapeXml(c.name || "category")}</image:caption>
    </image:image>
  </url>`;
        }
      }

      // 3. Fetch verified wholesale suppliers
      const sRes = await fetch(`${supabaseUrl}/rest/v1/business_table?select=user_id,legal_name,city,region,logo_url,updated_at&limit=200`, { headers });
      if (sRes.ok) {
        const suppliers = await sRes.json();
        for (const s of suppliers) {
          const lastMod = (s.updated_at || new Date().toISOString()).split("T")[0];
          const supplierImg = s.logo_url || APP_ICON_URL;
          const locText = s.city ? ` in ${s.city}, ${s.region || "Uganda"}` : " in Kampala, Uganda";
          xml += `
  <url>
    <loc>https://${WEB_APP_HOST}/suppliers?id=${encodeURIComponent(s.user_id)}</loc>
    <lastmod>${lastMod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.85</priority>
    <image:image>
      <image:loc>${escapeXml(supplierImg)}</image:loc>
      <image:title>${escapeXml(s.legal_name || "Verified Wholesale Supplier")}</image:title>
      <image:caption>Verified B2B Supplier${escapeXml(locText)} on N.O.D.E.</image:caption>
    </image:image>
  </url>`;
        }
      }
    } catch (err) {
      console.error("Error generating sitemap products:", err);
    }
  }

  xml += `
</urlset>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml;charset=UTF-8",
      "Cache-Control": "public, max-age=43200, s-maxage=86400",
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

function escapeXml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
