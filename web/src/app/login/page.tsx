"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { GoudanLogo } from "@/components/brand/GoudanLogo";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { Loader2, Eye, EyeOff } from "lucide-react";

const loginSchema = z.object({
  email: z.string().email("请输入有效的邮箱地址"),
  password: z.string().min(1, "请输入密码"),
});

type LoginFormData = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const router = useRouter();
  const { auth: authRepo } = useRepositories();
  const { user, login, loading: authLoading, error, clearError } = useAuthStore();
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      email: "user@goudan.app",
      password: "123456",
    },
  });

  // Check existing session
  useEffect(() => {
    useAuthStore.getState().checkSession(authRepo);
  }, [authRepo]);

  // Redirect if already logged in
  useEffect(() => {
    if (user) {
      router.replace("/chat");
    }
  }, [user, router]);

  const onSubmit = async (data: LoginFormData) => {
    clearError();
    try {
      await login(authRepo, data.email, data.password);
      router.replace("/chat");
    } catch {
      // error is set in store
    }
  };

  if (authLoading && !user) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center h-full bg-background">
      <Card className="w-full max-w-sm mx-4 bg-surface">
        <CardHeader className="text-center pb-2 pt-6">
          <div className="flex justify-center mb-4">
            <GoudanLogo size={48} showText={false} />
          </div>
          <CardTitle className="text-lg">登录苟蛋</CardTitle>
        </CardHeader>
        <CardContent className="px-6 pb-6">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">邮箱</Label>
              <Input
                id="email"
                type="email"
                placeholder="user@goudan.app"
                {...register("email")}
              />
              {errors.email && (
                <p className="text-xs text-destructive">{errors.email.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  placeholder="••••••"
                  {...register("password")}
                  className="pr-9"
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              {errors.password && (
                <p className="text-xs text-destructive">{errors.password.message}</p>
              )}
            </div>

            {error && (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  登录中...
                </>
              ) : (
                "登录"
              )}
            </Button>

            <p className="text-xs text-center text-muted-foreground">
              Mock 登录 — 邮箱: user@goudan.app, 密码: 123456
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
