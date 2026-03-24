<?php
/**
 * Plugin Name: JPTB Article Fetcher
 * Description: Fetches pending articles from GitHub and creates WordPress drafts hourly.
 * Version: 1.0
 */

define('JPTB_GITHUB_RAW', 'https://raw.githubusercontent.com/JPTB-1/japan-travel-base-automation/main/pending_articles/');

// ── Schedule ─────────────────────────────────────────────────────────────────

register_activation_hook(__FILE__, 'jptb_activate');
function jptb_activate() {
    if (!wp_next_scheduled('jptb_fetch_event')) {
        wp_schedule_event(time(), 'hourly', 'jptb_fetch_event');
    }
}

register_deactivation_hook(__FILE__, 'jptb_deactivate');
function jptb_deactivate() {
    wp_clear_scheduled_hook('jptb_fetch_event');
}

add_action('jptb_fetch_event', 'jptb_fetch_and_create_posts');

// ── Manual trigger via ?jptb_fetch=1 (admin only) ────────────────────────────

add_action('init', function() {
    if (isset($_GET['jptb_fetch']) && current_user_can('manage_options')) {
        jptb_fetch_and_create_posts();
        wp_die('JPTB: fetch complete. Check drafts.');
    }
});

// ── Core fetch logic ──────────────────────────────────────────────────────────

function jptb_fetch_and_create_posts() {
    $index_url = JPTB_GITHUB_RAW . 'index.json?t=' . time();
    $response  = wp_remote_get($index_url, ['timeout' => 15]);

    if (is_wp_error($response)) {
        error_log('[JPTB] Failed to fetch index: ' . $response->get_error_message());
        return;
    }

    $index = json_decode(wp_remote_retrieve_body($response), true);
    if (empty($index['files'])) {
        return;
    }

    $processed = get_option('jptb_processed_files', []);

    foreach ($index['files'] as $filename) {
        if (in_array($filename, $processed, true)) {
            continue;
        }

        $article_url = JPTB_GITHUB_RAW . $filename . '?t=' . time();
        $resp        = wp_remote_get($article_url, ['timeout' => 15]);

        if (is_wp_error($resp)) {
            error_log('[JPTB] Failed to fetch ' . $filename . ': ' . $resp->get_error_message());
            continue;
        }

        $article = json_decode(wp_remote_retrieve_body($resp), true);
        if (empty($article['title']) || empty($article['content'])) {
            error_log('[JPTB] Invalid article JSON: ' . $filename);
            continue;
        }

        // Get or create category
        $category_id = 0;
        if (!empty($article['category_slug'])) {
            $term = get_term_by('slug', $article['category_slug'], 'category');
            if ($term) {
                $category_id = $term->term_id;
            } else {
                $result = wp_insert_term(
                    $article['category_name'] ?? $article['category_slug'],
                    'category',
                    ['slug' => $article['category_slug']]
                );
                if (!is_wp_error($result)) {
                    $category_id = $result['term_id'];
                }
            }
        }

        // Create draft post
        $post_data = [
            'post_title'    => wp_strip_all_tags($article['title']),
            'post_content'  => $article['content'],
            'post_status'   => 'draft',
            'post_author'   => 1,
            'post_category' => $category_id ? [$category_id] : [],
        ];

        $post_id = wp_insert_post($post_data, true);

        if (is_wp_error($post_id)) {
            error_log('[JPTB] Failed to insert post: ' . $post_id->get_error_message());
            continue;
        }

        // Set meta description for SEO plugins
        if (!empty($article['meta_description'])) {
            update_post_meta($post_id, '_yoast_wpseo_metadesc', $article['meta_description']);
            update_post_meta($post_id, '_aioseo_description', $article['meta_description']);
            update_post_meta($post_id, 'rank_math_description', $article['meta_description']);
        }

        // Upload and set featured image
        if (!empty($article['featured_image_b64'])) {
            $image_data = base64_decode($article['featured_image_b64']);
            if ($image_data) {
                $upload = wp_upload_bits(
                    sanitize_file_name($article['title']) . '.png',
                    null,
                    $image_data
                );
                if (empty($upload['error'])) {
                    $attachment_id = wp_insert_attachment([
                        'post_mime_type' => 'image/png',
                        'post_title'     => sanitize_text_field($article['title']),
                        'post_status'    => 'inherit',
                    ], $upload['file'], $post_id);

                    if (!is_wp_error($attachment_id)) {
                        require_once ABSPATH . 'wp-admin/includes/image.php';
                        wp_update_attachment_metadata(
                            $attachment_id,
                            wp_generate_attachment_metadata($attachment_id, $upload['file'])
                        );
                        set_post_thumbnail($post_id, $attachment_id);
                        error_log('[JPTB] Featured image set for post ' . $post_id);
                    }
                }
            }
        }

        error_log('[JPTB] Created draft post ID ' . $post_id . ': ' . $article['title']);

        $processed[] = $filename;
        update_option('jptb_processed_files', $processed);
    }
}
