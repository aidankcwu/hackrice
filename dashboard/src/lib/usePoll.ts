"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResult } from "./api";

export function usePoll<T>(fn:()=>Promise<ApiResult<T>>,intervalMs:number) {
  const fnRef=useRef(fn); fnRef.current=fn;
  const [data,setData]=useState<T>(); const [mock,setMock]=useState(false); const [error,setError]=useState<string>();
  const refresh=useCallback(async()=>{try{const result=await fnRef.current();setData(result.data);setMock(result.mock);setError(undefined);}catch(e){setError(e instanceof Error?e.message:"Request failed");}},[]);
  useEffect(()=>{void refresh();const timer=window.setInterval(refresh,intervalMs);return()=>window.clearInterval(timer);},[intervalMs,refresh]);
  return {data,mock,error,refresh};
}
