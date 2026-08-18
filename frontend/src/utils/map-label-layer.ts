export const ROUTE_LABEL_ZOOMS: [number, number] = [3, 20];

export function routeLabelLayerOptions(): {
  collision: false;
  allowCollision: false;
  zooms: [number, number];
  zIndex: number;
} {
  return {
    collision: false,
    allowCollision: false,
    zooms: [...ROUTE_LABEL_ZOOMS],
    zIndex: 240,
  };
}
