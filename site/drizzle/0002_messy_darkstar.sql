CREATE TABLE `lead_changes` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`folio` text NOT NULL,
	`before_json` text,
	`after_json` text NOT NULL,
	`changed_by` text NOT NULL,
	`changed_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
