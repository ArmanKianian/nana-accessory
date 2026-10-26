import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwind from "@tailwindcss/vite";

const site = "https://nana-accessory.ir";

export default defineConfig({
  site,
  integrations: [sitemap()],
  vite: { plugins: [tailwind()] }
});
