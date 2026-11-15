CREATE TABLE `server_sync_keys` (
	`dataset` text NOT NULL,
	`scope` text NOT NULL,
	`generation` text NOT NULL,
	`source_key` text NOT NULL,
	`page_id` text NOT NULL,
	PRIMARY KEY(`dataset`, `scope`, `generation`, `source_key`)
);
--> statement-breakpoint
CREATE TABLE `server_sync_pages` (
	`id` text PRIMARY KEY NOT NULL,
	`dataset` text NOT NULL,
	`scope` text NOT NULL,
	`generation` text NOT NULL,
	`page_no` integer NOT NULL,
	`revision` text NOT NULL,
	`content_hash` text NOT NULL,
	`source_rows` integer NOT NULL,
	`matched_rows` integer NOT NULL,
	`source_saved_count` integer NOT NULL,
	`source_total` integer NOT NULL,
	`source_status` text NOT NULL,
	`source_updated_at` text NOT NULL,
	`received_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_server_sync_page` ON `server_sync_pages` (`dataset`,`scope`,`generation`,`page_no`);