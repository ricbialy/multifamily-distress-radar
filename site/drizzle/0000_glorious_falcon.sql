CREATE TABLE `radar_snapshots` (
	`city_slug` text PRIMARY KEY NOT NULL,
	`payload_json` text NOT NULL,
	`generated_at` text NOT NULL,
	`received_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`source` text DEFAULT 'scheduled-refresh' NOT NULL
);
