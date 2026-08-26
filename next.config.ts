import type { NextConfig } from "next";

const githubPages = process.env.GITHUB_PAGES === "true";
const repositoryBasePath = "/simulated-mobility-explorer";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  assetPrefix: githubPages ? repositoryBasePath : "",
};

export default nextConfig;
