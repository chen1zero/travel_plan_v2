import type {
  DailyItinerary,
  PlanEnvelope,
  RouteMode,
  RouteSegment,
  TravelRequest,
} from "../types/travel";

const mode = (
  distance: number,
  minutes: number,
): RouteMode => ({
  available: true,
  distance_km: distance,
  duration_minutes: minutes,
  error: null,
});

const route = (
  id: string,
  sequence: number,
  origin: string,
  originAddress: string,
  destination: string,
  destinationAddress: string,
  recommended: RouteSegment["recommended_mode"],
  times: [number, number, number],
): RouteSegment => ({
  route_id: id,
  sequence,
  origin: { name: origin, address: originAddress, city: "北京" },
  destination: {
    name: destination,
    address: destinationAddress,
    city: "北京",
  },
  walking: mode(Number((times[0] * 0.075).toFixed(1)), times[0]),
  driving: mode(Number((times[1] * 0.36).toFixed(1)), times[1]),
  public_transit: mode(
    Number((times[2] * 0.16).toFixed(1)),
    times[2],
  ),
  recommended_mode: recommended,
  recommendation_reason:
    recommended === "walking"
      ? "距离适中，沿途街区适合步行感受城市"
      : recommended === "public_transit"
        ? "时间稳定，换乘少，适合当天行程"
        : "携带行李时更方便，整体用时较短",
});

const hotel = {
  name: "北京王府井璞隐酒店",
  address: "北京市东城区王府井大街201号",
  longitude: 116.4109,
  latitude: 39.9163,
};

const itineraries: DailyItinerary[] = [
  {
    day: 1,
    date: "2026-08-01",
    theme: "皇城中轴 · 初见北京",
    weather_advice: "白天有短时阵雨，建议携带折叠伞，午后注意防暑。",
    schedule: [
      {
        schedule_item_id: "day1-palace",
        order: 1,
        time_slot: "09:00–12:00",
        place_name: "故宫博物院",
        address: "北京市东城区景山前街4号",
        activity: "沿中轴线参观三大殿与东西六宫，感受明清宫廷建筑。",
        duration_minutes: 180,
        notes: ["提前预约上午场", "建议从午门进入"],
        location: { longitude: 116.397, latitude: 39.9193 },
      },
      {
        schedule_item_id: "day1-jingshan",
        order: 2,
        time_slot: "13:30–15:00",
        place_name: "景山公园",
        address: "北京市西城区景山西街44号",
        activity: "登万春亭俯瞰故宫全景，补充中轴线视角。",
        duration_minutes: 90,
        notes: ["雨后石阶湿滑，请放慢脚步"],
        location: { longitude: 116.3966, latitude: 39.9251 },
      },
      {
        schedule_item_id: "day1-shichahai",
        order: 3,
        time_slot: "16:00–18:00",
        place_name: "什刹海历史文化街区",
        address: "北京市西城区地安门西大街49号",
        activity: "沿前海与烟袋斜街慢行，在胡同里结束第一天。",
        duration_minutes: 120,
        notes: ["晚餐可在地安门周边解决"],
        location: { longitude: 116.3862, latitude: 39.9413 },
      },
    ],
    routes: [
      route(
        "r1",
        1,
        hotel.name,
        hotel.address,
        "故宫博物院",
        "北京市东城区景山前街4号",
        "public_transit",
        [38, 18, 24],
      ),
      route(
        "r2",
        2,
        "故宫博物院",
        "北京市东城区景山前街4号",
        "景山公园",
        "北京市西城区景山西街44号",
        "walking",
        [14, 9, 18],
      ),
      route(
        "r3",
        3,
        "景山公园",
        "北京市西城区景山西街44号",
        "什刹海历史文化街区",
        "北京市西城区地安门西大街49号",
        "walking",
        [24, 13, 19],
      ),
    ],
    estimated_cost_cny: {
      transport: 18,
      tickets: 80,
      food: 180,
      hotel: 460,
      subtotal: 738,
      notes: ["酒店价格为演示估算，实际以下单页面为准"],
    },
  },
  {
    day: 2,
    date: "2026-08-02",
    theme: "国家记忆 · 城市文脉",
    weather_advice: "多云转晴，体感炎热，室内场馆与户外参观交替安排。",
    schedule: [
      {
        schedule_item_id: "day2-museum",
        order: 1,
        time_slot: "09:00–12:00",
        place_name: "中国国家博物馆",
        address: "北京市东城区东长安街16号",
        activity: "重点参观古代中国基本陈列，梳理中华文明时间线。",
        duration_minutes: 180,
        notes: ["须提前实名预约", "周一通常闭馆"],
        location: { longitude: 116.4011, latitude: 39.9051 },
      },
      {
        schedule_item_id: "day2-temple",
        order: 2,
        time_slot: "14:00–16:30",
        place_name: "天坛公园",
        address: "北京市东城区天坛东里甲1号",
        activity: "游览祈年殿、回音壁与圜丘，观察礼制建筑布局。",
        duration_minutes: 150,
        notes: ["园区较大，建议穿舒适鞋履"],
        location: { longitude: 116.4066, latitude: 39.8819 },
      },
      {
        schedule_item_id: "day2-qianmen",
        order: 3,
        time_slot: "17:30–19:30",
        place_name: "前门大街",
        address: "北京市东城区前门大街",
        activity: "从正阳门一路慢逛至鲜鱼口，体验老字号与街巷生活。",
        duration_minutes: 120,
        notes: ["晚餐建议预留 1 小时"],
        location: { longitude: 116.3978, latitude: 39.8994 },
      },
    ],
    routes: [
      route(
        "r4",
        1,
        hotel.name,
        hotel.address,
        "中国国家博物馆",
        "北京市东城区东长安街16号",
        "public_transit",
        [34, 16, 21],
      ),
      route(
        "r5",
        2,
        "中国国家博物馆",
        "北京市东城区东长安街16号",
        "天坛公园",
        "北京市东城区天坛东里甲1号",
        "public_transit",
        [42, 19, 28],
      ),
      route(
        "r6",
        3,
        "天坛公园",
        "北京市东城区天坛东里甲1号",
        "前门大街",
        "北京市东城区前门大街",
        "public_transit",
        [36, 18, 25],
      ),
    ],
    estimated_cost_cny: {
      transport: 22,
      tickets: 34,
      food: 210,
      hotel: 460,
      subtotal: 726,
      notes: [],
    },
  },
  {
    day: 3,
    date: "2026-08-03",
    theme: "园林诗意 · 松弛收尾",
    weather_advice: "午后可能有雷阵雨，颐和园建议上午游览并避开开阔水面。",
    schedule: [
      {
        schedule_item_id: "day3-summer",
        order: 1,
        time_slot: "08:30–12:00",
        place_name: "颐和园",
        address: "北京市海淀区新建宫门路19号",
        activity: "从东宫门进入，沿长廊步行至佛香阁与昆明湖。",
        duration_minutes: 210,
        notes: ["建议乘地铁前往", "雷雨时停止登高与游船"],
        location: { longitude: 116.2732, latitude: 39.9999 },
      },
      {
        schedule_item_id: "day3-library",
        order: 2,
        time_slot: "14:00–16:00",
        place_name: "国家图书馆",
        address: "北京市海淀区中关村南大街33号",
        activity: "在典籍博物馆安静收尾，预留室内行程应对降雨。",
        duration_minutes: 120,
        notes: ["携带有效身份证件"],
        location: { longitude: 116.3255, latitude: 39.9435 },
      },
    ],
    routes: [
      route(
        "r7",
        1,
        hotel.name,
        hotel.address,
        "颐和园",
        "北京市海淀区新建宫门路19号",
        "public_transit",
        [165, 52, 58],
      ),
      route(
        "r8",
        2,
        "颐和园",
        "北京市海淀区新建宫门路19号",
        "国家图书馆",
        "北京市海淀区中关村南大街33号",
        "public_transit",
        [95, 32, 39],
      ),
    ],
    estimated_cost_cny: {
      transport: 30,
      tickets: 30,
      food: 160,
      hotel: null,
      subtotal: 220,
      notes: ["最后一晚住宿取决于返程时间"],
    },
  },
];

export function createDemoPlan(request?: TravelRequest): PlanEnvelope {
  const city = request?.destination_city || "北京";
  const preferences =
    request?.preferences.length ? request.preferences : ["历史文化", "人文街区"];
  const budget = request?.budget_cny || 5000;

  return {
    plan_id: "plan_demo_beijing",
    task_id: "task_demo_beijing",
    session_id: "session_demo_beijing",
    previous_plan_id: null,
    revision: 1,
    status: "completed",
    generated_at: new Date().toISOString(),
    plan: {
      plan_version: "1.0",
      request_summary: {
        destination_city: city,
        start_date: request?.start_date || "2026-08-01",
        end_date: request?.end_date || "2026-08-03",
        days: 3,
        budget_cny: budget,
        preferences,
        hotel_requirement: request?.accommodation_type || "经济型",
        unresolved_fields: ["酒店实时房价", "部分场馆预约余量"],
      },
      weather_summary: [
        {
          date: "2026-08-01",
          day_weather: "阵雨",
          night_weather: "多云",
          min_temperature_c: 25,
          max_temperature_c: 32,
          advice: ["携带折叠伞", "注意防暑补水"],
        },
        {
          date: "2026-08-02",
          day_weather: "多云",
          night_weather: "晴",
          min_temperature_c: 24,
          max_temperature_c: 33,
          advice: ["午间注意防晒"],
        },
        {
          date: "2026-08-03",
          day_weather: "雷阵雨",
          night_weather: "阵雨",
          min_temperature_c: 23,
          max_temperature_c: 31,
          advice: ["上午优先户外行程", "雷雨时避免登高"],
        },
      ],
      selected_hotel: {
        name: hotel.name,
        address: hotel.address,
        selection_reason:
          "位于王府井核心区域，前往故宫与国家博物馆方便，周边公共交通完善。",
        price_cny_per_night: 460,
        booking_note: "演示价格仅作预算参考，实时房态需预订前确认。",
        location: {
          longitude: hotel.longitude,
          latitude: hotel.latitude,
        },
      },
      daily_itinerary: structuredClone(itineraries),
      budget_summary: {
        currency: "CNY",
        total_budget: budget,
        estimated_total: 3434,
        remaining: budget - 3434,
        breakdown: {
          transport: 220,
          tickets: 244,
          food: 690,
          hotel: 1380,
        },
        notes: ["预留约 900 元用于临时消费与价格波动"],
      },
      booking_and_safety_tips: [
        "故宫和国家博物馆均建议提前通过官方渠道预约。",
        "北京夏季午后容易出现雷阵雨，户外行程应保留调整空间。",
        "地图时间来自实时路线服务，出发前请再次查看交通状况。",
      ],
      data_notes: [
        "当前页面使用演示规划数据。",
        "接通后端接口后，天气、POI 与路线数据将由高德服务实时提供。",
      ],
    },
  };
}
