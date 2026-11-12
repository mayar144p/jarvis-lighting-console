/* A stand-in for the NDI runtime, for tools/ndicheck.mjs: the functions the
   desk calls, with the real headers' struct layouts (NDI SDK 6).  Two
   sources ("TEST (Red)" and "TEST (Bars)"); frames 64 x 36 RGBA with padded
   rows; what is sent is kept so the check can read it back. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif

typedef struct { const char* p_ndi_name; const char* p_url_address; } NDIlib_source_t;
typedef struct { bool show_local_sources; const char* p_groups; const char* p_extra_ips; } NDIlib_find_create_t;
typedef struct { NDIlib_source_t source_to_connect_to; int color_format; int bandwidth; bool allow_video_fields; const char* p_ndi_recv_name; } NDIlib_recv_create_v3_t;
typedef struct {
  int xres, yres; uint32_t FourCC; int frame_rate_N, frame_rate_D; float picture_aspect_ratio; int frame_format_type;
  int64_t timecode; uint8_t* p_data; int line_stride_in_bytes; const char* p_metadata; int64_t timestamp;
} NDIlib_video_frame_v2_t;
typedef struct { const char* p_ndi_name; const char* p_groups; bool clock_video, clock_audio; } NDIlib_send_create_t;

static NDIlib_source_t SOURCES[2] = { { "TEST (Red)", "127.0.0.1:5961" }, { "TEST (Bars)", "127.0.0.1:5962" } };
static int outstanding = 0, finds = 0, recvs = 0, color_seen = -1, bw_seen = -1;
typedef struct { int red; int n; } recv_t;
typedef struct { char name[64]; } send_t;
static int sent = 0, sent_w = 0, sent_h = 0, sent_stride = 0; static uint32_t sent_sum = 0, sent_fourcc = 0;
static uint8_t sent_first[4]; static int64_t sent_tc = 0; static char sent_name[64];

API bool NDIlib_initialize(void) { return true; }
API const char* NDIlib_version(void) { return "NDI SDK stand-in 6.0"; }
API void* NDIlib_find_create_v2(const NDIlib_find_create_t* p) { finds++; return (void*)SOURCES; }
API void NDIlib_find_destroy(void* p) { finds--; }
API const NDIlib_source_t* NDIlib_find_get_current_sources(void* p, uint32_t* n) { *n = 2; return SOURCES; }
API void* NDIlib_recv_create_v3(const NDIlib_recv_create_v3_t* p) {
  if (!p || !p->source_to_connect_to.p_ndi_name) return NULL;
  recv_t* r = calloc(1, sizeof(recv_t));
  r->red = strstr(p->source_to_connect_to.p_ndi_name, "Red") != NULL;
  color_seen = p->color_format; bw_seen = p->bandwidth; recvs++;
  return r;
}
API void NDIlib_recv_destroy(void* p) { free(p); recvs--; }
API int NDIlib_recv_capture_v2(void* p, NDIlib_video_frame_v2_t* v, void* a, void* m, uint32_t timeout) {
  recv_t* r = p;
  if (r->n % 3 == 2) { r->n++; return 0; }          /* now and then nothing waiting */
  r->n++;
  int w = 64, h = 36, stride = w * 4 + 16;
  uint8_t* d = malloc(stride * h);
  for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
    uint8_t* px = d + y * stride + x * 4;
    if (r->red) { px[0] = 255; px[1] = 0; px[2] = 0; }
    else { int bar = x * 8 / w; px[0] = (bar & 1) ? 255 : 0; px[1] = (bar & 2) ? 255 : 0; px[2] = (bar & 4) ? 255 : 0; }
    px[3] = 255;
  }
  memset(d + w * 4, 0xAB, 16);                     /* (the padding: must not show) */
  memset(v, 0, sizeof(*v));
  v->xres = w; v->yres = h; v->FourCC = 0x41424752; v->frame_rate_N = 30000; v->frame_rate_D = 1001;
  v->picture_aspect_ratio = (float)w / h; v->frame_format_type = 1; v->p_data = d; v->line_stride_in_bytes = stride;
  outstanding++;
  return 1;
}
API void NDIlib_recv_free_video_v2(void* p, const NDIlib_video_frame_v2_t* v) { if (v && v->p_data) { free(v->p_data); outstanding--; } }
API void* NDIlib_send_create(const NDIlib_send_create_t* p) {
  send_t* s = calloc(1, sizeof(send_t));
  strncpy(sent_name, p && p->p_ndi_name ? p->p_ndi_name : "", 63);
  return s;
}
API void NDIlib_send_destroy(void* p) { free(p); }
API void NDIlib_send_send_video_v2(void* p, const NDIlib_video_frame_v2_t* v) {
  sent++; sent_w = v->xres; sent_h = v->yres; sent_stride = v->line_stride_in_bytes; sent_fourcc = v->FourCC; sent_tc = v->timecode;
  uint32_t s = 0;
  for (int i = 0; i < v->yres * v->line_stride_in_bytes; i++) s = s * 31 + v->p_data[i];
  sent_sum = s; memcpy(sent_first, v->p_data, 4);
}
API int NDIlib_send_get_no_connections(void* p, uint32_t t) { return 1; }

/* what the check reads back */
API int fake_outstanding(void) { return outstanding; }
API int fake_recvs(void) { return recvs; }
API int fake_color(void) { return color_seen; }
API int fake_bandwidth(void) { return bw_seen; }
API int fake_sent(void) { return sent; }
API int fake_sent_w(void) { return sent_w; }
API int fake_sent_h(void) { return sent_h; }
API int fake_sent_stride(void) { return sent_stride; }
API uint32_t fake_sent_fourcc(void) { return sent_fourcc; }
API uint32_t fake_sent_sum(void) { return sent_sum; }
API int64_t fake_sent_tc(void) { return sent_tc; }
API const char* fake_sent_name(void) { return sent_name; }
API uint32_t fake_sent_first(void) { return sent_first[0] | (sent_first[1] << 8) | (sent_first[2] << 16) | ((uint32_t)sent_first[3] << 24); }
