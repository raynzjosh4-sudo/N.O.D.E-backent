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

    // 6. Detect Search Engine Bots & Social Media Scrapers
    const userAgent = (request.headers.get("User-Agent") || "").toLowerCase();
    const isBot = /whatsapp|facebookexternalhit|twitterbot|linkedinbot|slackbot|discordbot|telegrambot|applebot|googlebot|bingbot|yandex|duckduckbot|skypeuripreview|pinterest|baiduspider/.test(
      userAgent
    );

    // If bot requesting a product:
    if (isBot && productId) {
      const product = await fetchProductFromSupabase(productId, env);
      if (product) {
        return generateProductHtmlResponse(product, url);
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
 * Return JSON share metadata for apps
 */
function generateProductJsonResponse(product, url) {
  const name = product.name || "Wholesale Product";
  const brand = product.brand || "";
  const srp = Number(product.srp) || 0;
  const priceFormatted = srp > 0 ? `UGX ${srp.toLocaleString("en-US")}` : "";
  const imageUrl = product.image_url || (Array.isArray(product.media_urls) && product.media_urls[0]) || FALLBACK_IMAGE;
  const warehouse = product.warehouse_loc || "Kampala, Uganda";
  const shareUrl = `https://${WEB_APP_HOST}/product/specs/${product.id}`;
  const shareText = `Check out ${name} on N.O.D.E Wholesale for ${priceFormatted}!\n${shareUrl}`;

  return Response.json(
    {
      id: product.id,
      name,
      brand,
      price: srp,
      currency: "UGX",
      priceFormatted,
      imageUrl,
      warehouse,
      moq: product.moq || 1,
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
  const name = product.name || "Wholesale Product";
  const brand = product.brand ? `${product.brand} ` : "";
  const srp = Number(product.srp) || 0;
  const priceFormatted = srp > 0 ? `UGX ${srp.toLocaleString("en-US")}` : "";
  const priceDisplay = priceFormatted ? `Wholesale ${priceFormatted}` : "Wholesale Direct";

  const metaTitle = `${name} — ${priceDisplay} | N.O.D.E.`;
  const imageUrl = product.image_url || (Array.isArray(product.media_urls) && product.media_urls[0]) || FALLBACK_IMAGE;
  const warehouse = product.warehouse_loc || "Kampala, Uganda";
  const moqText = product.moq ? ` • Min. Order: ${product.moq} Units` : "";
  const rawDesc =
    product.seo_description ||
    product.description ||
    `Wholesale ${name} available directly from verified suppliers in ${warehouse} on N.O.D.E. (National Online Distribution & Enterprise).`;
  const cleanDesc = rawDesc.replace(/\s+/g, " ").trim().slice(0, 200);

  const metaDescription = `${priceFormatted ? `${priceFormatted} / unit${moqText} • ` : ""}Supplier: ${
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
      price: srp > 0 ? String(srp) : "0",
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
  ${srp > 0 ? `<meta property="product:price:amount" content="${srp}">\n  <meta property="product:price:currency" content="UGX">` : ""}

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
    <p style="font-size: 20px; color: #12BBF0; font-weight: bold; margin-bottom: 12px;">${escapeHtml(priceFormatted)} / unit</p>
    <p style="font-size: 14px; color: #8B949E; line-height: 1.6; margin-bottom: 24px;">${escapeHtml(cleanDesc)}</p>
    <a href="${escapeHtml(url.pathname)}" style="display: inline-block; background: #12BBF0; color: #FFF; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 16px;">
      Open in N.O.D.E. App
    </a>
  </div>
  <script>
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
      const endpoint = `${supabaseUrl}/rest/v1/products?select=id,name,brand,srp,image_url,media_urls,updated_at,created_at&order=created_at.desc&limit=2000`;
      const res = await fetch(endpoint, { headers });

      if (res.ok) {
        const products = await res.json();
        for (const p of products) {
          const lastMod = (p.updated_at || p.created_at || new Date().toISOString()).split("T")[0];
          const img = p.image_url || (Array.isArray(p.media_urls) && p.media_urls[0]);
          const brandText = p.brand ? ` - ${escapeXml(p.brand)}` : "";

          xml += `
  <url>
    <loc>https://${WEB_APP_HOST}/product/specs/${p.id}</loc>
    <lastmod>${lastMod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>${
      img
        ? `
    <image:image>
      <image:loc>${escapeXml(img)}</image:loc>
      <image:title>${escapeXml(p.name || "Wholesale Product")}</image:title>
      <image:caption>Wholesale price UGX ${Number(p.srp || 0).toLocaleString("en-US")}${brandText}</image:caption>
    </image:image>`
        : ""
    }
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
