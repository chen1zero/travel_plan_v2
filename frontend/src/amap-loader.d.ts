declare module "@amap/amap-jsapi-loader" {
  interface AMapLoadOptions {
    key: string;
    version: string;
    plugins?: string[];
  }

  const AMapLoader: {
    load(options: AMapLoadOptions): Promise<unknown>;
  };

  export default AMapLoader;
}

declare interface Window {
  _AMapSecurityConfig?: {
    securityJsCode?: string;
    serviceHost?: string;
  };
}
