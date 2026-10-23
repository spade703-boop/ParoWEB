// MySekai 抓包上报脚本（Shadowrocket §http-response）
//
// 职责只有三步：取原始响应体 → 转成字节 → POST 到 ParoWEB。
// 任何环节失败都必须 $done({}) 放行，绝不能影响游戏本身。
//
// 部署前需要改两处：
//   ENDPOINT —— 你的站点地址
//   TOKEN    —— 下载时由服务端注入，不要在仓库里写死

var ENDPOINT = "https://akitoya.top/api/v1/mysekai/ingest";
var TOKEN = "__TOKEN__";
var DEBUG = false; // true 时不真正上报，只打印诊断信息

(function () {
  var url = $request && $request.url ? $request.url : "";
  var matched = /\/api\/user\/(\d+)\/mysekai(?:\/birthday-party\/(\d+)\/delivery)?(?:\?.*)?$/.exec(url);
  if (!matched) {
    $done({});
    return;
  }
  var gameId = matched[1];
  var bytes = toBytes($response.body);

  if (!bytes || bytes.length === 0) {
    log("empty body");
    $done({});
    return;
  }
  if (DEBUG) {
    log("game_id=" + gameId + " bytes=" + bytes.length + " head=" + hexHead(bytes));
    $done({});
    return;
  }

  $httpClient.post(
    {
      url: ENDPOINT,
      headers: {
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/octet-stream",
        "X-Mysekai-Game-Id": gameId,
        "X-Mysekai-Raw-Bytes": String(bytes.length),
        "X-Mysekai-Event": matched[2] ? "birthday_delivery" : "snapshot",
        "X-Mysekai-Party-Id": matched[2] || ""
      },
      body: bytes,
      timeout: 30
    },
    function (error, response, data) {
      if (error) {
        log("upload failed: " + error);
      }
      // 无论成败都放行原始响应
      $done({});
    }
  );
})();

// 不同引擎对二进制响应体的表示不一样，这里做兼容：
// 1) Uint8Array / ArrayBuffer（最理想）
// 2) bodyBytes 字段（部分引擎提供）
// 3) 字符串，每个字符对应一个字节（Latin-1 风格）
function toBytes(body) {
  if (!body) return null;
  if (typeof Uint8Array !== "undefined" && body instanceof Uint8Array) return body;
  if (typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer) {
    return new Uint8Array(body);
  }
  if (body.bodyBytes) return toBytes(body.bodyBytes);
  if (typeof body === "string") {
    var out = new Uint8Array(body.length);
    for (var i = 0; i < body.length; i++) {
      out[i] = body.charCodeAt(i) & 0xff;
    }
    return out;
  }
  if (typeof body.byteLength === "number") {
    return new Uint8Array(body);
  }
  return null;
}

function hexHead(bytes) {
  var parts = [];
  for (var i = 0; i < bytes.length && i < 16; i++) {
    parts.push(("0" + bytes[i].toString(16)).slice(-2));
  }
  return parts.join(" ");
}

function log(message) {
  if (typeof console !== "undefined" && console.log) console.log("[mysekai] " + message);
}
