CREATE TABLE `jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`mode` text NOT NULL,
	`day` text NOT NULL,
	`state` text NOT NULL,
	`page` integer NOT NULL,
	`page_size` integer NOT NULL,
	`total` integer,
	`saved` integer NOT NULL,
	`calls` integer NOT NULL,
	`max_payload` integer NOT NULL,
	`lease` text,
	`lease_until` integer NOT NULL,
	`error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `receipts` (
	`job_id` text NOT NULL,
	`page` integer NOT NULL,
	`count` integer NOT NULL,
	`bytes` integer NOT NULL,
	`hash` text NOT NULL,
	`committed_at` text NOT NULL,
	PRIMARY KEY(`job_id`, `page`)
);
--> statement-breakpoint
CREATE TABLE `records` (
	`job_id` text NOT NULL,
	`source_id` text NOT NULL,
	`title` text NOT NULL,
	`agency` text NOT NULL,
	`amount` text NOT NULL,
	`raw` text NOT NULL,
	PRIMARY KEY(`job_id`, `source_id`)
);
--> statement-breakpoint
CREATE TABLE `usage` (
	`day` text PRIMARY KEY NOT NULL,
	`calls` integer NOT NULL
);
