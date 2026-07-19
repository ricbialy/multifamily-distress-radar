import { drizzle } from "drizzle-orm/d1";
import * as schema from "./schema";

type RadarEnv = { DB?: D1Database; INGEST_TOKEN?: string };

export function getRadarEnv(): RadarEnv {
  return (globalThis as unknown as { __RADAR_ENV?: RadarEnv }).__RADAR_ENV ?? {};
}

export function getDb() {
  const env = getRadarEnv();
  if (!env.DB) throw new Error("Cloudflare D1 binding `DB` is unavailable");
  return drizzle(env.DB, { schema });
}
